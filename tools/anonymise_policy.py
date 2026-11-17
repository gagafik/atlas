"""What a pseudonymised copy of a school database may take from the school.

Default deny: only the tables named in TABLES are written, and every column of
such a table has a rule. A table that is not here is not copied; a column
without a rule stops the tool before it writes anything. Rules:

  keep    ids, numbers, dates, enums, booleans; never a name or free text
  drop    NULL or '' as the column allows
  person  a person's name: its pseudonym; an unknown name gets a generic invented one
  text    free text, kept only if it holds no real-name token, otherwise hidden
  names   text in which known full names are replaced by their pseudonyms first
  number  like keep, for a technical number that may hold 10 or more digits (the gate does not take it for a phone)
  hash    a value that only has to stay equal to itself: salted digest of a random per-run salt
  json:X  a JSON document walked leaf by leaf with the profile X (see PROFILES)
"""
from dataclasses import dataclass, field, replace

HIDDEN = '(текст скрыт)'

KEEP, DROP, PERSON, TEXT, NAMES, HASH, NUMBER = 'keep', 'drop', 'person', 'text', 'names', 'hash', 'number'
SIMPLE = {KEEP, DROP, PERSON, TEXT, NAMES, HASH, NUMBER}


@dataclass(frozen=True)
class Profile:
    """drop: key -> replacement; const: dotted path -> value; people: dotted paths whose string leaf is a person's name.
    A dotted path lists the keys only: a list adds nothing, so `lesson.students.name` is the name of an item of a list."""
    drop: dict = field(default_factory=dict)
    const: dict = field(default_factory=dict)
    people: frozenset = frozenset()
    text: frozenset = frozenset()  # leaves that are free text: hidden whole when they hold a real name, never rewritten


PROFILES = {
    # The school state (and the timetable templates, which hold the same lists): names of people become pseudonyms.
    'state': Profile(
        drop={'externalId': None, 'rosterNote': '', 'rosterSources': [], 'note': ''},
        const={'settings.name': 'Школа (обезличенная копия)', 'absences.name': 'Отсутствие', 'importInfo.filename': '', 'importInfo.notes': []},
        people=frozenset({'teachers.name', 'students.name'})),
    # A journal document: a comment on a pupil is a note about a child; files and materials are not copied.
    'journal': Profile(
        drop={'comment': '', 'homeworkFiles': [], 'materials': [], 'curriculum': None, 'bookingId': None},
        people=frozenset({'lesson.students.name'}), text=frozenset({'content.topic', 'content.homework'})),
    'coverage': Profile(),
    'event': Profile(drop={'comment': ''}),
    'grading': Profile(
        drop={'comment': '', 'explanation': '', 'reason': ''},
        people=frozenset({'student.name', 'teachers.name', 'author', 'approval.actor', 'actor'})),
    'settings': Profile(),
}

# (table -> column -> rule). Every column of the application's own schema is listed.
K = KEEP
TABLES = {
    # The school and its timetable. `history` is not copied: the latest state is `workspaces`.
    'workspaces': dict(id=K, revision=K, document='json:state'),
    'timetable_versions': dict(workspace=K, valid_from=K, valid_to=K, revision=K, created=K, document='json:state'),
    'timetable_current': dict(workspace=K, valid_from=K),
    'pupil_enrollments': dict(workspace=K, student_id=K, kind=K, target_id=K, start_day=K, end_day=K),
    # The journal.
    'journal_entries': dict(workspace=K, lesson_key=K, version=K, updated=K, actor_name=PERSON, document='json:journal',
                            published_document='json:journal', published_version=K, published_at=K),
    'journal_members': dict(workspace=K, lesson_key=K, student_id=K, name=PERSON, kind=K),
    'journal_attendance': dict(workspace=K, lesson_key=K, student_id=K, attendance=K, late_minutes=K, day=K, start=K, end=K,
                               subject_id=K, left_at=K, absence_code=TEXT),
    'journal_attendance_lessons': dict(workspace=K, lesson_key=K, day=K, start=K, end=K, subject_id=K, subject=TEXT, group_name=TEXT,
                                       teachers=NAMES, version=K, published_at=K),
    'journal_attendance_epoch': dict(workspace=K, version=K),
    'journal_periods': dict(workspace=K, id=K, name=TEXT, start_day=K, end_day=K, kind=K, version=K, status=K, ever_closed=K, updated=K),
    'journal_period_closures': dict(workspace=K, period_id=K, version=K, created=K, actor=PERSON, reason=TEXT, school_revision=K,
                                    counts=K, published_count=K, draft_changes=K, state_document='json:state'),
    'journal_period_snapshots': dict(workspace=K, period_id=K, closure_version=K, lesson_key=K, published_version=K, published_at=K, day=K, start=K),
    'journal_period_documents': dict(workspace=K, lesson_key=K, published_version=K, teacher_ids=K, document='json:journal'),
    'journal_coverage_documents': dict(workspace=K, lesson_key=K, version=K, published_version=K, lesson='json:coverage', topic_filled=K,
                                       draft_teachers=K, record_ids=K, unmarked_ids=K),
    'journal_coverage_teachers': dict(workspace=K, teacher_id=K, lesson_key=K, day=K),
    'journal_projection_versions': dict(name=K, version=K),
    # Marks.
    'grade_events': dict(seq=K, workspace=K, student_id=K, lesson_key=K, assessment_id=K, created=K, change=K, document='json:event', hidden=K),
    'assessment_category_config': dict(workspace=K, version=K, document='json:settings'),
    'grading_scales': dict(workspace=K, id=K, version=K, document='json:settings'),
    'grading_scale_versions': dict(workspace=K, id=K, version=K, document='json:settings', actor=PERSON, created=K),
    # `assigned` is the id of an account of the school: not copied; the accounts step assigns the rows of the copy's teacher.
    'grading_books': dict(workspace=K, id=K, period_id=K, closure_version=K, created=K, source_kind=K),
    'grading_rows': dict(workspace=K, id=K, book_id=K, student_id=K, subject_id=K, class_id=K, assigned=DROP, version=K, status=K,
                         source='json:grading', proposal='json:grading'),
    'grading_facts': dict(id=K, workspace=K, lesson_key=K, version=K, student_id=K, subject_id=K, day=K, start=K,
                          summary='json:grading', works='json:grading'),
    'grading_projected': dict(workspace=K, lesson_key=K, version=K, format=K),
    'final_results': dict(workspace=K, period_id=K, student_id=K, subject_id=K, row_id=K, version=K, document='json:grading'),
    'grading_report_settings': dict(workspace=K, period_id=K, class_id=K, version=K, document='json:settings'),
    # Homework tasks only: no answers, files or histories. `assignment` is a digest of the task; equal tasks keep equal digests.
    'homework_queue_tasks': dict(workspace=K, lesson_key=K, sort_date=K, lesson_date=K, due_date=K, subject=TEXT, group_name=TEXT,
                                 preview=TEXT, assignment=HASH, source_kind=K, source_id=K, draft_teachers=K, late_policy=K,
                                 group_id=K, subject_id=K, submission=K),
    'homework_queue_versions': dict(workspace=K, version=K),
}

# Tables the second step (accounts of the copy) fills through the application; the gate lets them hold rows.
ACCOUNT_TABLES = frozenset({'accounts', 'family_links'})
# Tables that the accounts step empties again: sessions of the sign-in checks and the journals the application writes while it makes accounts.
# Anything else that is filled and not listed fails the gate.
ACCOUNT_CLEARED = ('sessions', 'audit', 'login_journal', 'notifications', 'family_history')

# Documents that hold the lists of teachers and pupils of a school: their names are the core of the registry.
STATE_DOCUMENTS = [('workspaces', 'document'), ('history', 'document'), ('redo_history', 'document'), ('variants', 'document'),
                   ('previews', 'document'), ('generation_jobs', 'document'), ('timetable_versions', 'document'),
                   ('journal_period_closures', 'state_document'), ('school_years', 'document')]
# Columns of person ids, checked for opacity because ids are kept as they are.
STUDENT_ID_COLUMNS = [('journal_members', 'student_id'), ('pupil_enrollments', 'student_id'), ('journal_attendance', 'student_id'),
                      ('grade_events', 'student_id'), ('grading_rows', 'student_id'), ('final_results', 'student_id'), ('grading_facts', 'student_id')]
TEACHER_ID_COLUMNS = [('journal_coverage_teachers', 'teacher_id')]

# Columns of ANY source table that hold a person's name, copied or not. `name` only counts in these tables.
NAME_TABLES = {'accounts', 'journal_members', 'quiz_members', 'meeting_hosts', 'school_form_recipients', 'presence'}
NAME_COLUMNS = {'actor', 'author'}
NAME_SUFFIXES = ('actor_name', 'author_name', 'student_name', 'account_name', 'user_name', 'parent_name', 'teacher_name', 'host_name',
                 'creator_name', 'guardian_name', 'pupil_name', 'owner_name', 'sender_name', 'signer_name', 'reviewer_name', 'member_name')
# Inside JSON documents of any table: a `name` under one of these keys, and these keys themselves, are people.
NAME_PARENT_KEYS = {'student', 'students', 'teacher', 'teachers', 'parent', 'parents', 'guardian', 'guardians', 'host', 'hosts', 'member',
                    'members', 'author', 'actor', 'user', 'creator', 'owner', 'invitee', 'invitees', 'pupil', 'pupils', 'recipient', 'recipients'}
NAME_LEAF_KEYS = {'author', 'actor', 'actorName', 'authorName', 'studentName', 'parentName', 'teacherName', 'guardianName', 'fullName',
                  'userName', 'hostName', 'creatorName'}
# System actors are not people: their words are not names to look for.
IGNORED_NAMES = {'система', 'локальный пользователь', 'администратор'}


@dataclass(frozen=True)
class Policy:
    tables: dict = field(default_factory=lambda: {t: dict(c) for t, c in TABLES.items()})
    profiles: dict = field(default_factory=lambda: dict(PROFILES))

    def rule(self, table, column):
        return self.tables.get(table, {}).get(column)

    def without(self, table, column):
        """A copy of the policy with a column left without a rule (for tests of the fail-closed behaviour)."""
        return replace(self, tables={**self.tables, table: {c: r for c, r in self.tables[table].items() if c != column}})

    def with_rule(self, table, column, rule):
        return replace(self, tables={**self.tables, table: {**self.tables[table], column: rule}})

    def profile(self, rule):
        return self.profiles[rule.split(':', 1)[1]]

    def problems(self):
        """Rules that name nothing known; the policy test and the tool both refuse such a policy."""
        found = []
        for table, columns in self.tables.items():
            for column, rule in columns.items():
                if rule not in SIMPLE and not (rule.startswith('json:') and rule[5:] in self.profiles):
                    found.append(f'{table}.{column}')
        return found


POLICY = Policy()
