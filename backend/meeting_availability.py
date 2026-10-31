"""Read-only occupancy checks. Meeting slots never write timetable data."""
from datetime import datetime
from zoneinfo import ZoneInfo

ZONE=ZoneInfo('Asia/Qyzylorda')


class Occupancy:
    def __init__(self,snap): self.snap,self.days,self.kinds=snap,{},{}

    def index(self,day):
        """Busy intervals of one date by (teacher|room, id). Resolving a date is slow, so each date is resolved once per Occupancy."""
        if day not in self.days:
            index={}
            for row in self.snap.lesson_rows(day):
                if row['status']=='cancelled':continue
                for kind,ids in [('teacher',row['teacherIds']),('room',row['roomIds'])]:
                    for id in ids:index.setdefault((kind,id),[]).append((row['start'],row['end']))
            self.days[day]=index
        return self.days[day]

    def conflict(self,start,end,teacher,room):
        a=datetime.fromisoformat(start).astimezone(ZONE);b=datetime.fromisoformat(end).astimezone(ZONE)
        day=a.date();start_time=a.strftime('%H:%M');end_time=b.strftime('%H:%M')
        index=self.index(day)
        for kind,id in [('teacher',teacher),('room',room)]:
            if id and any(start_time<right and left<end_time for left,right in index.get((kind,id),[])):
                return 'Учитель занят в школьном календаре.' if kind=='teacher' else 'Кабинет занят в школьном календаре.'
        if teacher and any(x.teacherId==teacher and x.start<=day<=x.end and start_time<x.endTime and x.startTime<end_time for x in self.snap.state.absences):
            return 'В школьном календаре указано отсутствие учителя.'
        return None

    def closed(self,day,class_id=None):
        """Name of the calendar closure ('cancel' effect) that stops school on this date, else None.

        With a class: a closure of all classes or of that class. Without: only a closure that together covers every class."""
        found=[e for e in self.snap.state.events if e.effect=='cancel' and e.start<=day<=e.end]
        if class_id:return next((e.name for e in found if not e.classIds or class_id in e.classIds),None)
        classes={c.id for c in self.snap.state.classes};covered=set()
        for e in found:covered|=set(e.classIds) if e.classIds else classes
        return found[0].name if found and classes<=covered else None
