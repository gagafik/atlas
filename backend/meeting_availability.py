"""Read-only occupancy checks. Meeting slots never write timetable data."""
from datetime import datetime
from zoneinfo import ZoneInfo

ZONE=ZoneInfo('Asia/Qyzylorda')


class Occupancy:
    def __init__(self,snap): self.snap,self.days=snap,{}

    def conflict(self,start,end,teacher,room):
        a=datetime.fromisoformat(start).astimezone(ZONE);b=datetime.fromisoformat(end).astimezone(ZONE)
        day=a.date();start_time=a.strftime('%H:%M');end_time=b.strftime('%H:%M')
        if day not in self.days:
            index={}
            for row in self.snap.lesson_rows(day):
                if row['status']=='cancelled':continue
                for kind,ids in [('teacher',row['teacherIds']),('room',row['roomIds'])]:
                    for id in ids:index.setdefault((kind,id),[]).append((row['start'],row['end']))
            self.days[day]=index
        for kind,id in [('teacher',teacher),('room',room)]:
            if id and any(start_time<right and left<end_time for left,right in self.days[day].get((kind,id),[])):
                return 'Учитель занят в школьном календаре.' if kind=='teacher' else 'Кабинет занят в школьном календаре.'
        if teacher and any(x.teacherId==teacher and x.start<=day<=x.end and start_time<x.endTime and x.startTime<end_time for x in self.snap.state.absences):
            return 'В школьном календаре указано отсутствие учителя.'
        return None
