"""Media containers, scoped uploads and authorized byte ranges on private files."""
from io import BytesIO
import hashlib
import json
import struct
import wave
import pytest
from fastapi import HTTPException
from backend import api, homework_files, teaching_media
from test_access import client, signed
from test_teaching_materials import setup, upload, save, download
from test_live_lessons import setup as live_setup, new, question, ok, cmd, url, current, action


def wav_bytes():
    stream = BytesIO()
    with wave.open(stream, 'wb') as w:
        w.setnchannels(1); w.setsampwidth(2); w.setframerate(8000)
        w.writeframes(b'\0\0' * 8000)
    return stream.getvalue()


def box(kind, data): return struct.pack('!I', 8 + len(data)) + kind + data


def containers():
    mp4 = box(b'ftyp', b'isom\0\0\0\0mp42') + box(b'moov', b'synthetic metadata') + box(b'mdat', b'synthetic samples')
    # These fixtures test container recognition, not codec decodability.
    mp3 = (b'\xff\xfb\x90\0' + bytes(413)) * 2
    webm = b'\x1a\x45\xdf\xa3\x87\x42\x82\x84webm\x18\x53\x80\x67\xff' + b'synthetic segment'
    ogg = b'OggS\x00\x02' + bytes(20) + b'\x01\x13' + b'OpusHead' + bytes(11)
    return {'.wav': wav_bytes(), '.mp3': mp3, '.m4a': mp4, '.mp4': mp4, '.webm': webm, '.ogg': ogg}


@pytest.mark.parametrize('extension', list(teaching_media.MEDIA))
def test_library_upload_recognizes_scoped_formats_and_download_types(client, extension):
    teacher, _, pupil, _ = setup(client); data = containers()[extension]
    file = ok(upload(teacher, data, 'sample' + extension))
    material = ok(save(teacher, fileIds=[file['id']]))
    response = download(teacher, file['id'], material)
    assert response.content == data and response.headers['content-type'] == teaching_media.MEDIA[extension]
    assert response.headers['content-disposition'].startswith('inline;')
    assert response.headers['accept-ranges'] == 'bytes'
    assert response.headers['cache-control'] == 'no-store' and response.headers['vary'] == 'Cookie'
    forced = download(teacher, file['id'], material, download='true')
    assert forced.content == data and forced.headers['content-disposition'].startswith('attachment;')
    assert forced.headers['content-type'] == 'application/octet-stream'
    assert download(pupil, file['id'], material).status_code == 403
    with pytest.raises(HTTPException): homework_files.name_for('sample' + extension)
    with api.connect() as db:
        assert db.execute('SELECT sha256 FROM teaching_material_files WHERE id=?', (file['id'],)).fetchone()[0] == hashlib.sha256(data).hexdigest()


@pytest.mark.parametrize('extension', list(teaching_media.MEDIA))
def test_mislabelled_truncated_media_is_rejected_and_reservation_cleaned(client, extension):
    teacher, _, _, _ = setup(client)
    for data in (b'<html>wrong format</html>', containers()[extension][:10]):
        assert upload(teacher, data, 'fake' + extension).status_code == 422
    with api.connect() as db:
        assert db.execute('SELECT count(*) FROM teaching_material_files').fetchone()[0] == 0
        assert homework_files.total_stored(db) == 0


def test_material_ranges_if_range_and_revocation(client):
    teacher, other, _, _ = setup(client); data = wav_bytes()
    file = ok(upload(teacher, data, 'recording.wav'))
    material = ok(save(teacher, visibility='staff', fileIds=[file['id']]))
    path = f'/api/journal/material-files/{file["id"]}?workspace=demo&materialId={material["id"]}&version=1'
    full = other.get(path)
    part = other.get(path, headers={'Range': 'bytes=20-43', 'If-Range': full.headers['etag']})
    assert part.status_code == 206 and part.content == data[20:44]
    assert part.headers['content-range'] == f'bytes 20-43/{len(data)}'
    assert part.headers['content-length'] == '24'
    suffix = other.get(path, headers={'Range': 'bytes=-8'})
    assert suffix.status_code == 206 and suffix.content == data[-8:]
    multiple = other.get(path, headers={'Range': 'bytes=0-3,20-23'})
    assert multiple.status_code == 206 and len(multiple.content) == int(multiple.headers['content-length'])
    assert b'RIFF' in multiple.content and b'bytes 20-23/' in multiple.content
    assert other.get(path, headers={'Range': 'bytes=99999999-'}).status_code == 416
    assert other.get(path, headers={'Range': 'bytes=0-5', 'If-Range': '"old"'}).content == data
    ok(save(teacher, material, visibility='private'))
    denied = other.get(path, headers={'Range': 'bytes=0-15', 'If-Range': full.headers['etag']})
    assert denied.status_code == 404 and 'content-range' not in denied.headers


def test_live_ranges_do_not_bypass_question_reveal_or_current_membership(client, live_setup):
    teacher, pupil, parent, _ = live_setup; data = wav_bytes()
    file = ok(upload(teacher, data, 'answer.wav')); material = ok(save(teacher, fileIds=[file['id']]))
    q = question(teacher); w = api.read('demo').workloads[0]
    definition = dict(title='Audio response', groupId=w.groupId, subjectId=w.subjectId,
        questions=[dict(id=q['id'], version=q['version'], solutionMaterials=[dict(id=material['id'], version=1)])])
    id = ok(teacher.post(url(), json=cmd(definition=definition)))['id']
    path = url(f'/{id}/files/{file["id"]}?questionIndex=0&part=solutionMaterials')
    headers = {'Range': 'bytes=0-63'}
    ok(action(teacher, id, 'start')); ok(action(teacher, id, 'open'))
    for c in [pupil, parent]: assert c.get(path, headers=headers).status_code == 404
    assert teacher.get(path + '&projection=true', headers=headers).status_code == 404
    ok(action(teacher, id, 'close')); ok(action(teacher, id, 'reveal'))
    for c in [pupil, parent]:
        response = c.get(path, headers=headers)
        assert response.status_code == 206 and response.content == data[:64]
        assert response.headers['cache-control'] == 'no-store'
    assert teacher.get(path + '&projection=true', headers=headers).status_code == 206
    state = api.read('demo'); state.students[0].active = False; api.save('demo', state)
    for c in [pupil, parent]: assert c.get(path, headers=headers).status_code in {403,404}


def test_media_upload_finish_rechecks_account_and_retains_quotas(client, monkeypatch):
    teacher, _, _, _ = setup(client); data = wav_bytes(); original = teaching_media.validate_file
    def revoked(path, name):
        digest = original(path, name)
        with api.connect() as db: db.execute("UPDATE accounts SET version=version+1 WHERE login='teacher'")
        return digest
    monkeypatch.setattr(teaching_media, 'validate_file', revoked)
    assert upload(teacher, data, 'private.wav').status_code == 409
    with api.connect() as db: assert db.execute('SELECT count(*) FROM teaching_material_files').fetchone()[0] == 0


def test_container_boundaries_and_names(tmp_path):
    cases = [(b'ID3\x04\0\0\x7f\x7f\x7f\x7f'+bytes(50), '.mp3'),
             (b'RIFF\xff\xff\xff\xffWAVE'+bytes(30), '.wav'),
             (box(b'ftyp', b'avif'+bytes(4))+box(b'moov', b'x')+box(b'mdat', b'x'), '.mp4'),
             (struct.pack('!I', 0xffffffff)+b'ftyp'+bytes(32), '.mp4'),
             (containers()['.webm'].replace(b'webm',b'html'), '.webm'),
             (containers()['.ogg'].replace(b'OpusHead',b'<script>'), '.ogg')]
    path = tmp_path/'media.bin'
    for data, ext in cases:
        path.write_bytes(data)
        with pytest.raises(HTTPException): teaching_media.validate_file(path, 'bad'+ext)
    assert teaching_media.name_for('../safe.MP4') == 'safe.MP4'
    for name in ['x\n.mp4', 'x'*180+'.wav', 'x.svg', 'x.html']:
        with pytest.raises(HTTPException): teaching_media.name_for(name)
