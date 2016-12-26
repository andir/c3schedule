from copy import deepcopy
from unittest import TestCase

from c3schedule_irc import Schedule, diff_schedules


class TestScheduleDiff(TestCase):
    def setUp(self):
        self.schedule_json = dict(
            version='CANT REPRODUCE',
            conference=dict(
                acronym='33c3',
                title='33. Chaos Communication Congress',
                start='2016-12-27',
                end='2016-12-30',
                daysCount=4,
                timeslot_duration='00:15',
                days=[
                    dict(
                        index=0,
                        date='2016-12-27',
                        day_start='2016-12-27T10:00:00+01:00',
                        day_end='2016-12-27T04:00:00+01:00',
                        rooms={
                            'Saal 1': [
                                dict(
                                    id=123,
                                    guid='b8e0eb47-4832-4726-bc9b-9015bd96becf',
                                    logo=None,
                                    date='2016-12-27T11:00:00+01:00',
                                    start='11:00',
                                    duration='00:30',
                                    room='Saal 1',
                                    slug='foo-bar-baz',
                                    title='lol',
                                    subtitle='',
                                    track='CCC',
                                    type='lecture',
                                    language='en',
                                    abstract='',
                                    description='',
                                    recording_license='',
                                    do_not_record=False,
                                    persons=[],
                                    links=[],
                                    attachments=[]
                                )
                            ]
                        }
                    )
                ]
            )
        )
        self.schedule = Schedule.from_json(self.schedule_json)

    def test_schedule_equal(self):
        new_schedule_json = deepcopy(self.schedule_json)
        schedule = Schedule.from_json(new_schedule_json)

        self.assertEqual(self.schedule.conference.days[0].rooms['Saal 1'].sessions[123], schedule.conference.days[0].rooms['Saal 1'].sessions[123])

    def test_schedule_addition(self):
        new_schedule_json = deepcopy(self.schedule_json)
        new_schedule_json['version'] = 'ff'
        new_schedule_json['conference']['days'][0]['rooms']['Saal 1'].append(
            dict(
                id=1243,
                guid='xxxxx-4832-4726-bc9b-9015bd96becf',
                logo=None,
                date='2016-12-27T12:00:00+01:00',
                start='11:00',
                duration='01:30',
                room='Saal 1',
                slug='foo-xxx-baz',
                title='loxxxl',
                subtitle='',
                track='CCC',
                type='lecture',
                language='en',
                abstract='',
                description='',
                recording_license='',
                do_not_record=False,
                persons=[],
                links=[],
                attachments=[]
            )
        )
        new_schedule = Schedule.from_json(new_schedule_json)

        changed, added, missing = diff_schedules(self.schedule, new_schedule)

        self.assertEqual(len(changed), 0)
        self.assertEqual(len(added), 1)
        self.assertEqual(len(missing), 0)

    def test_schedule_removed(self):
        new_schedule_json = deepcopy(self.schedule_json)
        new_schedule_json['version'] = 'ff'
        new_schedule_json['conference']['days'][0]['rooms']['Saal 1'] = []

        new_schedule = Schedule.from_json(new_schedule_json)

        changed, added, missing = diff_schedules(self.schedule, new_schedule)

        self.assertEqual(len(changed), 0)
        self.assertEqual(len(added), 0)
        self.assertEqual(len(missing), 1)

    def test_schedule_modified(self):
        new_schedule_json = deepcopy(self.schedule_json)
        new_schedule_json['version'] = 'ff'
        new_schedule_json['conference']['days'][0]['rooms']['Saal 1'][0]['date'] = '2016-12-27T12:00:00+01:00'

        new_schedule = Schedule.from_json(new_schedule_json)

        changed, added, missing = diff_schedules(self.schedule, new_schedule)

        self.assertEqual(len(changed), 1)
        self.assertEqual(len(added), 0)
        self.assertEqual(len(missing), 0)

    def test_schedule_modified_moved_date(self):
        new_schedule_json = deepcopy(self.schedule_json)
        new_schedule_json['version'] = 'ff'
        new_schedule_json['conference']['days'][0]['rooms']['Saal 1'] = []
        new_schedule_json['conference']['days'].append(dict(
            index=1,
            date='2016-12-28',
            day_start='2016-12-28T10:00:00+01:00',
            day_end='2016-12-29T04:00:00+01:00',
            rooms={
                'Saal 1': [
                    dict(
                        id=123,
                        guid='b8e0eb47-4832-4726-bc9b-9015bd96becf',
                        logo=None,
                        date='2016-12-28T11:00:00+01:00',
                        start='11:00',
                        duration='00:30',
                        room='Saal 1',
                        slug='foo-bar-baz',
                        title='lol',
                        subtitle='',
                        track='CCC',
                        type='lecture',
                        language='en',
                        abstract='',
                        description='',
                        recording_license='',
                        do_not_record=False,
                        persons=[],
                        links=[],
                        attachments=[]
                    )
                ]
            }
        )
        )

        new_schedule = Schedule.from_json(new_schedule_json)

        changed, added, missing = diff_schedules(self.schedule, new_schedule)

        self.assertEqual(len(added), 0)
        self.assertEqual(len(missing), 0)
        self.assertEqual(len(changed), 1)
