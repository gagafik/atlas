"""The feed of announcements with replies and «нравится».

A reply is a post of the announcement's thread that points at its root post (reply_to). It never moves latest_id,
so history bounds, search, read markers and acknowledgements of the announcement stay as they were."""
from fastapi import HTTPException, Query, Request
from pydantic import Field

from .access import now
from .models import Model
from .notifications import encoded

MODES = ['off', 'author', 'all']
SHOWN = 3  # replies shown under an announcement before «Показать все»


class FeedReply(Model):
    requestId: str = Field(pattern=r'^[a-f0-9-]{36}$')
    text: str = Field(min_length=1, max_length=2000)


class Like(Model):
    liked: bool


class MessageFeed:
    def __init__(self, messages):
        self.messages, self.access = messages, messages.access

    def tables(self, db):
        db.execute('CREATE TABLE IF NOT EXISTS message_feed_settings (thread_id TEXT PRIMARY KEY,replies TEXT)')
        db.execute('CREATE TABLE IF NOT EXISTS message_likes (post_id INTEGER,user_id TEXT,created TEXT,PRIMARY KEY(post_id,user_id))')

    def set_mode(self, db, thread_id, mode):
        db.execute('INSERT INTO message_feed_settings VALUES (?,?) ON CONFLICT(thread_id) DO UPDATE SET replies=excluded.replies', (thread_id, mode))

    @staticmethod
    def effective(mode, directory):
        """Replies open to everyone need the channel; without it the stored choice falls back to the author alone."""
        return 'author' if mode == 'all' and 'feed_public_replies' not in directory.channels else mode

    def root(self, db, thread_id):
        row = db.execute('SELECT id FROM message_posts WHERE thread_id=? AND reply_to IS NULL ORDER BY id LIMIT 1', (thread_id,)).fetchone()
        return row[0] if row else None

    def hidden(self, db, ids):
        return {r[0] for r in db.execute('SELECT post_id FROM message_moderation WHERE hidden=1 AND post_id IN (SELECT value FROM json_each(?))', (encoded(list(ids)),))}

    @staticmethod
    def scope(thread, user, live, mode):
        """'every': the author reads all replies. 'public': a recipient in the open mode reads the replies written while it was open, and their own.
        'own': only the replies the account wrote itself (a recipient in author mode, or a former one)."""
        return 'every' if thread['authorId'] == user['id'] else 'public' if live and mode == 'all' else 'own'

    @staticmethod
    def readable(scope, user_id, author_id, public):
        return scope == 'every' or author_id == user_id or scope == 'public' and bool(public)

    def states(self, db, entries, user, directory):
        """Per thread id: the reply mode, counts and what the account may do. entries: [(thread, live link?)] of announcements."""
        ids = [t['id'] for t, _ in entries]
        if not ids: return {}
        encoded_ids = encoded(ids)
        modes = {r[0]: r[1] for r in db.execute('SELECT thread_id,replies FROM message_feed_settings WHERE thread_id IN (SELECT value FROM json_each(?))', (encoded_ids,))}
        roots = {}
        for thread_id, id in db.execute('SELECT thread_id,id FROM message_posts WHERE thread_id IN (SELECT value FROM json_each(?)) AND reply_to IS NULL ORDER BY id DESC', (encoded_ids,)): roots[thread_id] = id
        posts = encoded(list(roots.values()))
        counts = {r[0]: r[1:] for r in db.execute('SELECT reply_to,count(*),sum(author_id=?),sum(coalesce(reply_public,0)=1 OR author_id=?) FROM message_posts WHERE reply_to IN (SELECT value FROM json_each(?)) GROUP BY reply_to', (user['id'], user['id'], posts))}
        likes = {r[0]: r[1] for r in db.execute('SELECT post_id,count(*) FROM message_likes WHERE post_id IN (SELECT value FROM json_each(?)) GROUP BY post_id', (posts,))}
        liked = {r[0] for r in db.execute('SELECT post_id FROM message_likes WHERE user_id=? AND post_id IN (SELECT value FROM json_each(?))', (user['id'], posts))}
        hidden = self.hidden(db, roots.values())
        result = {}
        for thread, live in entries:
            root = roots.get(thread['id']); mode = self.effective(modes.get(thread['id'], 'off'), directory)
            total, own, public = (n or 0 for n in counts.get(root, (0, 0, 0)))
            open_ = live and thread['status'] == 'open' and root not in hidden
            result[thread['id']] = dict(postId=root, repliesMode=mode, replyCount=dict(every=total, public=public, own=own)[self.scope(thread, user, live, mode)],
                                        likes=likes.get(root, 0), liked=root in liked, canLike=open_,
                                        canRespond=open_ and mode != 'off' and not (mode == 'author' and thread['authorId'] == user['id']))
        return result

    def detail(self, db, thread, user, directory, through):
        """Extra keys of an announcement in the conversation view; other kinds have none."""
        if thread['kind'] != 'announcement': return {}
        return self.states(db, [(thread, through is None)], user, directory)[thread['id']]

    def replies(self, db, thread, root, user, scope, *, before=None, limit=50):
        """Visible replies, oldest first within the page, with hidden ones masked. (items, next cursor)"""
        rows = db.execute('SELECT id,author_id,author_name,created,body FROM message_posts WHERE reply_to=? AND (? IS NULL OR id<?) AND (?=1 OR author_id=? OR (?=1 AND coalesce(reply_public,0)=1)) ORDER BY id DESC LIMIT ?',
                          (root, before, before, int(scope == 'every'), user['id'], int(scope == 'public'), limit + 1)).fetchall()
        hidden = self.hidden(db, [r[0] for r in rows[:limit]])
        items = [dict(id=r[0], authorId=r[1], author=r[2], created=r[3], hidden=r[0] in hidden, own=r[1] == user['id'],
                      text='Сообщение скрыто администратором.' if r[0] in hidden else r[4]) for r in reversed(rows[:limit])]
        return items, rows[limit - 1][0] if len(rows) > limit else None

    def thread_for(self, db, id, user, directory):
        """The thread with a live link of the account. An announcement needs no list of its recipients for that."""
        row = db.execute('SELECT kind FROM message_threads WHERE id=? AND workspace=?', (id, directory.workspace)).fetchone()
        accounts = {} if row and row[0] == 'announcement' else self.messages.thread_accounts(db, id, user)
        return self.messages.find(db, id, user, directory, accounts)[0]

    def register(self, app, Workspace):
        @app.get('/api/messages/feed')
        def feed(request: Request, workspace: Workspace='school', before: int | None=Query(default=None, ge=1)):
            user, directory = self.messages.context(request, workspace)
            with self.access.lock, self.access.connect() as db:
                self.messages.tables(db)
                rows = db.execute('''SELECT t.* FROM message_threads t JOIN message_members m ON m.thread_id=t.id AND m.user_id=?
                    WHERE t.workspace=? AND t.kind='announcement' AND (? IS NULL OR t.latest_id<?) ORDER BY t.latest_id DESC LIMIT 21''', (user['id'], workspace, before, before)).fetchall()
                found = []
                for row in rows[:20]:
                    thread = self.messages.thread(db, row); seen = self.messages.visible(db, thread, user, directory, {})
                    if seen: found.append((thread, *seen))
                states = self.states(db, [(t, through is None) for t, _, through in found], user, directory)
                ids = encoded([t['id'] for t, _, _ in found])
                roots = {r[0]: r for r in db.execute('SELECT thread_id,id,author_name,created,body FROM message_posts WHERE thread_id IN (SELECT value FROM json_each(?)) AND reply_to IS NULL ORDER BY id DESC', (ids,))}
                hidden = self.hidden(db, [s['postId'] for s in states.values()])
                files = self.messages.files.for_posts(db, [s['postId'] for s in states.values() if s['postId'] not in hidden])
                items = []
                for thread, member, through in found:
                    state = states[thread['id']]; root = roots.get(thread['id']); masked = state['postId'] in hidden
                    latest = thread['latestId'] if through is None else through
                    last = []
                    if state['replyCount']:
                        last = self.replies(db, thread, state['postId'], user, self.scope(thread, user, through is None, state['repliesMode']), limit=SHOWN)[0]
                    items.append(dict(id=thread['id'], title=thread['title'], status=thread['status'], version=thread['version'], author=root[2] if root else '', created=root[3] if root else thread['created'],
                                      text='Сообщение скрыто администратором.' if masked else root[4] if root else '', files=[] if masked else files.get(state['postId'], []),
                                      requireAck=thread['requireAck'], acknowledged=member[3] == thread['version'], unread=latest > member[2], readOnly=through is not None,
                                      latestId=latest, canManage=through is None and thread['managerId'] == user['id'], lastReplies=last) | state)
            return dict(items=items, nextBefore=rows[19][10] if len(rows) > 20 else None)

        @app.get('/api/messages/threads/{id}/replies')
        def listing(id: str, request: Request, workspace: Workspace='school', before: int | None=Query(default=None, ge=1)):
            user, directory = self.messages.context(request, workspace)
            with self.access.lock, self.access.connect() as db:
                self.messages.tables(db)
                row = db.execute('SELECT kind FROM message_threads WHERE id=? AND workspace=?', (id, workspace)).fetchone()
                thread, _, through = self.messages.readable(db, id, user, directory, {} if row and row[0] == 'announcement' else self.messages.thread_accounts(db, id, user))
                if thread['kind'] != 'announcement': raise HTTPException(422, 'Ответы бывают только под объявлением.')
                state = self.states(db, [(thread, through is None)], user, directory)[id]
                items, next_before = self.replies(db, thread, state['postId'], user, self.scope(thread, user, through is None, state['repliesMode']), before=before)
            return dict(items=items, nextBefore=next_before)

        @app.post('/api/messages/threads/{id}/replies')
        def respond(id: str, body: FeedReply, request: Request, workspace: Workspace='school'):
            user, directory = self.messages.context(request, workspace)
            if not body.text: raise HTTPException(422, 'Напишите ответ.')
            with self.access.lock, self.access.connect() as db:
                self.messages.tables(db); db.execute('BEGIN IMMEDIATE'); self.messages.recheck(db, user, directory)
                thread = self.thread_for(db, id, user, directory)
                prior, signature = self.messages.replay(db, user, body.requestId, dict(workspace=workspace, id=id, action='feed_reply', body=body.model_dump()))
                if prior: return dict(id=int(prior.rsplit(':', 1)[1]), postId=self.root(db, id))
                if thread['kind'] != 'announcement': raise HTTPException(422, 'Ответы бывают только под объявлением.')
                root = self.root(db, id)
                if thread['status'] != 'open' or root in self.hidden(db, [root]): raise HTTPException(409, 'Ответы под этим объявлением закрыты.')
                row = db.execute('SELECT replies FROM message_feed_settings WHERE thread_id=?', (id,)).fetchone()
                mode = self.effective(row[0] if row else 'off', directory)
                if mode == 'off': raise HTTPException(409, 'Автор не открыл ответы под этим объявлением.')
                if mode == 'author' and thread['authorId'] == user['id']: raise HTTPException(422, 'Автору объявления ответы приходят в личные сообщения: ответьте отправителю в личной переписке.')
                self.messages.rate(db, user)
                # Whether everybody may read the reply is fixed now: switching the channel later neither hides nor publishes it.
                post = db.execute('INSERT INTO message_posts (thread_id,author_id,author_name,created,body,reply_to,reply_public) VALUES (?,?,?,?,?,?,?)', (id, user['id'], user['name'], now().isoformat(), body.text, root, int(mode == 'all'))).lastrowid
                db.execute('INSERT INTO message_requests VALUES (?,?,?,?)', (user['id'], body.requestId, signature, f'{id}:{post}'))
                author = self.messages.accounts(db, user_ids=[thread['authorId']]).get(thread['authorId'])
                if author and author['id'] != user['id'] and directory.enabled(author):
                    self.messages.notifications.deliver(db, [author], workspace, thread['version'], 'message.feed_reply')
                self.access.record(db, 'message.feed_replied', workspace, detail=id)
            return dict(id=post, postId=root)

        @app.put('/api/messages/posts/{post_id}/like')
        def like(post_id: int, body: Like, request: Request, workspace: Workspace='school'):
            user, directory = self.messages.context(request, workspace)
            with self.access.lock, self.access.connect() as db:
                self.messages.tables(db); db.execute('BEGIN IMMEDIATE'); self.messages.recheck(db, user, directory)
                post = db.execute('SELECT thread_id,reply_to FROM message_posts WHERE id=?', (post_id,)).fetchone()
                if not post: raise HTTPException(404, 'Сообщение недоступно.')
                thread = self.thread_for(db, post[0], user, directory)
                if post[1] is not None or thread['kind'] != 'announcement': raise HTTPException(422, 'Отметка «нравится» ставится только объявлению.')
                if thread['status'] != 'open' or post_id in self.hidden(db, [post_id]): raise HTTPException(409, 'Объявление закрыто для отметок.')
                if body.liked: db.execute('INSERT OR IGNORE INTO message_likes VALUES (?,?,?)', (post_id, user['id'], now().isoformat()))
                else: db.execute('DELETE FROM message_likes WHERE post_id=? AND user_id=?', (post_id, user['id']))
                likes = db.execute('SELECT count(*) FROM message_likes WHERE post_id=?', (post_id,)).fetchone()[0]
            return dict(likes=likes, liked=body.liked)
