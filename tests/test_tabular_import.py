import base64
from io import BytesIO
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from openpyxl import Workbook

from backend import api
from backend.seed import initial_state
from backend.models import Teacher, TimetableRule
from backend.tabular import parse_file
from backend.importing import MappingRequest, mapped_state, inspect_sheet, TABLES
from backend.exporting import ExportRequest, workbook


def csv(text): return parse_file('input.csv',text.encode('utf-8'),';')


def mapped(s, package, table, mapping, **kw):
    return mapped_state(s,package,MappingRequest(token='fixture-token',revision=s.revision,table=table,mapping=mapping,**kw))


def test_csv_encoding_quoted_lists_and_header_offset():
    raw='Отчёт\nФИО;Класс;Группы\n"Имя; с разделителем";10A;"x;y"\n'.encode('cp1251')
    p=parse_file('people.csv',raw,';')
    grid=inspect_sheet(p,0,2)
    assert grid['rows'][0]['number']==3 and grid['rows'][0]['cells']==['Имя; с разделителем','10A','x;y']
    assert grid['rowCount']==1
    assert inspect_sheet(p,0,0)['rowCount']==3


def test_xlsx_sheets_formula_detection_and_unmapped_formulas():
    wb=Workbook();ws=wb.active;ws.title='Учителя';ws.append(['name','ignored']);ws.append(['Новый учитель','=1+1'])
    wb.create_sheet('Пустой');buf=BytesIO();wb.save(buf)
    p=parse_file('book.xlsx',buf.getvalue())
    assert len(p['sheets'])==2 and p['warnings']
    s=initial_state('demo')
    result=mapped(s,p,'teachers',{'name':0},mode='add')
    assert result['status']=='success'
    assert mapped(s,p,'teachers',{'name':1},mode='add')['errors'][0]['row']==2


def test_binary_xls_and_html_mime_expand_merged_cells_without_scripts():
    p=parse_file('old.xls',Path('tests/fixtures/legacy-import.xls').read_bytes())
    assert p['sheets'][0]['rows'][1]==['Лаборатория','24']
    raw=b'Content-Type: text/html; charset=utf-8\r\n\r\n<table><tr><th>name</th><th>capacity</th></tr><tr><td rowspan="2">Lab<script>bad()</script></td><td>20</td></tr><tr><td>30</td></tr></table>'
    p=parse_file('export.xls',raw)
    assert p['sheets'][0]['rows']==[['name','capacity'],['Lab','20'],['Lab','30']]


def test_flat_xml_attributes_and_child_fields_and_entity_rejection():
    for raw in [b'<teachers><teacher id="a" name="One"/></teachers>',b'<teachers><teacher><id>a</id><name>One</name></teacher></teachers>',b'<school><teachers columns="id,name"><teacher id="a" name="One"/></teachers></school>']:
        assert parse_file('table.xml',raw)['sheets'][0]['rows']==[['id','name'],['a','One']]
    with pytest.raises(ValueError): parse_file('bad.xml',b'<!DOCTYPE x [<!ENTITY y "z">]><x/>')
    with pytest.raises(ValueError): parse_file('bad.xlsx',b'not a zip')
    with pytest.raises(ValueError): parse_file('bad.html',b'<table><tr><td colspan="100000000">x</td></tr></table>')
    with pytest.raises(ValueError): parse_file('bad.roz',b'anything')


def test_upsert_updates_only_mapped_fields_and_is_idempotent():
    s=initial_state('demo');s.teachers[0].unavailable=['0:1'];before=s.model_dump_json()
    p=csv('id;name\nt0;Новое имя\nnew;Новый учитель\n')
    result=mapped(s,p,'teachers',{'id':0,'name':1})
    assert result['status']=='success' and result['counts']['added']==result['counts']['changed']==1
    candidate=result['candidate']
    assert candidate.teachers[0].unavailable==['0:1']
    assert candidate.placements==s.placements and candidate.workloads==s.workloads
    assert s.model_dump_json()==before
    again=mapped(candidate,p,'teachers',{'id':0,'name':1})
    assert again['counts']['unchanged']==2 and again['candidate']==candidate


def test_exact_names_and_class_disambiguate_pupils_but_not_duplicate_teachers():
    s=initial_state('demo');s.students[0].name=s.students[2].name='Одинаковое имя'
    p=csv('name;class;active\nОдинаковое имя;10A;нет\n')
    result=mapped(s,p,'students',{'name':0,'classId':1,'active':2},match='name')
    assert not result['candidate'].students[0].active and result['candidate'].students[2].active
    s.teachers.append(Teacher(id='duplicate',name=s.teachers[0].name))
    p=csv('name\n'+s.teachers[0].name)
    assert mapped(s,p,'teachers',{'name':0},match='name')['status']=='errors'
    p=csv('id;teacher\nw0;'+s.teachers[0].name)
    result=mapped(s,p,'workloads',{'id':0,'teacherId':1})
    assert 'Неоднозначное' in result['errors'][0]['message']


def test_external_pupil_id_does_not_rename_internal_id_or_require_class_for_update():
    s=initial_state('demo');s.students[0].externalId='E-1'
    p=csv('code;name\nE-1;Уточнённое имя\n')
    r=mapped(s,p,'students',{'externalId':0,'name':1},match='externalId')
    assert r['candidate'].students[0].id=='s1' and r['candidate'].students[0].name=='Уточнённое имя'


def test_missing_references_duplicate_rows_and_update_only_fail_atomically():
    s=initial_state('demo')
    p=csv('id;teacher\nw0;t0\nw1;Неизвестный\n')
    result=mapped(s,p,'workloads',{'id':0,'teacherId':1})
    assert result['status']=='errors' and 'candidate' not in result and result['errorCount']==1
    assert result['errors'][0]['row']==3
    for p in [csv('id;name\nt0;Первое\nt0;Второе'),csv('id;name\nmissing;Новое')]:
        assert mapped(s,p,'teachers',{'id':0,'name':1},mode='update')['status']=='errors'
    assert mapped(s,csv('name\nПовтор\nПовтор'),'teachers',{'name':0},match='name')['status']=='errors'


def test_blank_policy_constants_lists_and_unmapped_identity():
    s=initial_state('demo');s.rooms[0].capacity=30
    p=csv('id;capacity\nr0;\n')
    assert mapped(s,p,'rooms',{'id':0,'capacity':1})['candidate'].rooms[0].capacity==30
    assert mapped(s,p,'rooms',{'id':0,'capacity':1},skipBlanks=False)['candidate'].rooms[0].capacity is None
    p=csv('id;name;classes;students\nnew-group;Курс;10A|10D;s1|s3\n')
    r=mapped(s,p,'groups',{'id':0,'name':1,'classIds':2,'studentIds':3},constants={'kind':'elective','rosterStatus':'complete'},listSeparator='|')
    assert r['candidate'].groups[-1].classIds==['c-demo-a','c-demo-d']
    assert r['candidate'].groups[-1].studentIds==['s1','s3']
    p=csv('id;name\n;Без ID\n')
    r=mapped(s,p,'teachers',{'id':0,'name':1},mode='add',skipBlanks=False)
    assert r['candidate'].teachers[-1].id.startswith('import-')


def test_reference_validation_of_unmapped_fields_and_rule_scope():
    s=initial_state('demo')
    p=csv('id;classes\ng-math;10A\n')
    assert mapped(s,p,'groups',{'id':0,'classIds':1})['errors'][0]['field']=='Связи базы'
    s.rules=[TimetableRule(id='rule',name='Максимум',kind='daily_load',scope='teacher',targetIds=['t0'],maxLessons=5)]
    p=csv('id;target;scope\nrule;Учитель физики;\n')
    r=mapped(s,p,'rules',{'id':0,'targetIds':1,'scope':2})
    assert r['candidate'].rules[0].targetIds==['t1']


def test_exported_entity_sheets_reimport_without_field_loss():
    s=initial_state('demo')
    package=parse_file('data.xlsx',workbook(s,ExportRequest(revision=0,format='xlsx_data')))
    from backend.exporting import TABLES
    for i,sheet in enumerate(package['sheets']):
        table=next((key for key,label in TABLES.items() if label==sheet['name']),None)
        if not table or len(sheet['rows'])<2: continue
        mapping={key:col for col,key in enumerate(sheet['rows'][0])}
        r=mapped(s,package,table,mapping,sheet=i,skipBlanks=False)
        assert r['status']=='success', (table,r)
        assert r['candidate']==s, table


def test_import_schema_endpoint_describes_every_importable_table(tmp_path,monkeypatch):
    monkeypatch.setattr(api,'DB',tmp_path/'import.db')
    r=TestClient(api.app).get('/api/exchange/import-schema')
    assert r.status_code==200,r.text
    schema={t['key']:t for t in r.json()}
    assert set(schema)==set(TABLES) and all(t['fields'] for t in schema.values())
    assert schema['students']['canMatchExternal'] and not schema['teachers']['canMatchExternal']
    assert next(f for f in schema['profiles']['fields'] if f['key']=='periods')['kind']=='list'


def test_api_upload_preview_scope_and_apply_revision(tmp_path,monkeypatch):
    monkeypatch.setattr(api,'DB',tmp_path/'import.db')
    client=TestClient(api.app)
    initial=client.get('/api/state?workspace=demo').json()
    raw='id;name\nt0;Из файла\n'.encode()
    response=client.post('/api/exchange/inspect?workspace=demo',json=dict(filename='teachers.csv',content=base64.b64encode(raw).decode(),delimiter=';'))
    assert response.status_code==200,response.text
    token=response.json()['token']
    assert client.post('/api/exchange/sheet',json=dict(token=token)).status_code==404
    result=client.post('/api/exchange/import-preview?workspace=demo',json=dict(token=token,revision=0,table='teachers',mapping={'id':0,'name':1})).json()
    assert result['status']=='success' and result['preview']['canApply']
    assert client.get('/api/state?workspace=demo').json()==initial
    p=result['preview']
    assert client.post('/api/editor/apply?workspace=demo',json=dict(revision=0,token=p['token'])).status_code==200
    assert client.post('/api/editor/apply?workspace=demo',json=dict(revision=0,token=p['token'])).status_code==409
    assert client.post('/api/undo?workspace=demo',json=dict(revision=1)).json()['teachers']==initial['teachers']


def test_import_that_introduces_timetable_conflicts_cannot_apply(tmp_path,monkeypatch):
    monkeypatch.setattr(api,'DB',tmp_path/'conflict.db')
    client=TestClient(api.app);s=client.get('/api/state?workspace=demo').json()
    s['placements']=[dict(id='a',workloadId='w0',day=0,period=1,roomId='r0'),dict(id='b',workloadId='w2',day=1,period=1,roomId='r1')]
    saved=client.put('/api/state?workspace=demo',json=s).json()
    raw='id;day\nb;Понедельник\n'.encode()
    info=client.post('/api/exchange/inspect?workspace=demo',json=dict(filename='p.csv',content=base64.b64encode(raw).decode(),delimiter=';')).json()
    r=client.post('/api/exchange/import-preview?workspace=demo',json=dict(token=info['token'],revision=saved['revision'],table='placements',mapping={'id':0,'day':1})).json()
    assert r['status']=='success' and not r['preview']['canApply']
    assert client.post('/api/editor/apply?workspace=demo',json=dict(revision=saved['revision'],token=r['preview']['token'])).status_code==422
    assert client.get('/api/state?workspace=demo').json()==saved
