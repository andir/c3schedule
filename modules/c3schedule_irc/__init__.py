import re
import datetime
import functools
import logging
import hashlib
import threading

import jinja2
import dateutil.parser
import pendulum
import requests
import sopel.formatting
import sopel.module
from sopel.config import StaticSection
from sopel.config.types import ValidatedAttribute

logger = logging.getLogger(__name__)

hall_channels = {
    'rC1': '#rC3-one',
    'rC2': '#rC3-two',
    'chaosstudio-hamburg': '#rc3-csh',
    'restrealitaet': '#rC3-restrealitaet',
    'r3s - Monheim/Rhein': '#rc3-r3s',
    'franconiannet': '#rc3-franconiannet',
    'Chaos-West TV': '#rc3-cwtv',
    'hacc München / about:future': '#rc3-hacc',
    'xHain Berlin': '#rc3-xhain',
    #    'c-base Berlin': '#rc3-', # :'(
    'ChaosTrawler': '#rc3-chaostrawler',
    'ChaosZone TV Stream': '#rc3-chaoszone',
    'ChaosZone Workshops': '#rc3-chaoszone',
    'OIO/A:F Bühne': '#rc3-oio',
    'OIO/A:F Workshop': '#rc3-oio',
    'SZ Bühne': '#rc3-sendezentrum',
    'Wikipaka': '#rc3-wikipaka',
    'Wikipaka Meetups': '#rc3-wikipaka',
    'Bitwäscherei Zürich': '#rc3-bitwaescherei',
}

pendulum.set_to_string_format('%d.%m. %H:%M')

class ScheduleConfigSection(StaticSection):
    fahrplan_url = ValidatedAttribute('fahrplan_url',
                                      default="https://fahrplan.events.ccc.de/congress/{{year}}/Fahrplan/")
    url = ValidatedAttribute('url', default="https://fahrplan.events.ccc.de/congress/{{year}}/Fahrplan/schedule.json")
    session_url = ValidatedAttribute('session_url',
                                     default='https://fahrplan.events.ccc.de/congress/{{year}}/Fahrplan/events/{{id}}.html')
    topic_template = ValidatedAttribute('topic_template',
                                        default='{{acronym}} - {{title}} | {{start}} -> {{end}} | Day {{dayN}} | {{url}} | Query c3schedule with .help/.subscribe/.unsubscribe/.info/.schedule/.search/.nextup')
    channel_topic_template = ValidatedAttribute('channel_topic_template',
            default='{{ "{" }}{{session.room[0]}}{{ "}" }}{{ session.room[1:] }} @ {{ session.date }} | ' +
                    '{% if angel %}SA: {{ angel }} | {% endif %}' +
                    '({{session.language}}) {{ session.title }} [{{ session.id }}] {% if session.url(bot) != "N/A" %}{{ session.url(bot) }}{% endif %} | ' +
                    '{% if stream_url %}Stream: {{ stream_url }}{% endif %}' +
                    '{{ channel_topic_suffix }}')
    stream_url_template = ValidatedAttribute('stream_url_template',
            default='https://streaming.media.ccc.de/36c3/{{ stream_hall }}'
    )
    channel_topic_suffix = ValidatedAttribute('channel_topic_suffix', default='')
    channel = ValidatedAttribute('channel', default="#36c3-schedule")
    angel_channel = ValidatedAttribute('angel_channel', default='#signalangels')


def configure(config):
    logger.info('Adding custom config sections')
    config.define_section('c3schedule', ScheduleConfigSection)


def setup_database(db):
    logger.info('Setting up database')
    try:
        db.execute('SELECT * FROM c3schedule_subscriptions;')
    except:
        logger.info('No database tables found. Creating.')
        pass
    else:
        logger.info('Database tables found.')
        return

    db.execute(
        'CREATE TABLE c3schedule_subscriptions (id INTEGER PRIMARY KEY, nickserv_account STRING, session_id INTEGER);')
    db.execute(
        'CREATE UNIQUE INDEX c3schedule_subcsription_limit ON c3schedule_subscriptions(nickserv_account, session_id);')
    db.execute('CREATE INDEX c3schedule_subscription_session_idx ON c3schedule_subscriptions (session_id);')


def render_jinja(template, **kwargs):
    return jinja2.Environment().from_string(template).render(**kwargs)

def get_accounts_for_session_id(db, session_id):
    result = db.execute('SELECT nickserv_account FROM c3schedule_subscriptions WHERE session_id = ?', [session_id])
    if result is None:
        return []

    return [r[0] for r in result.fetchall()]


def add_nick_to_session_id(db, nick, session_id):
    db.execute('INSERT INTO c3schedule_subscriptions (nickserv_account, session_id) VALUES (?, ?)', [nick, session_id])


def del_nick_from_session_id(db, nick, session_id):
    db.execute('DELETE FROM c3schedule_subscriptions WHERE session_id=? AND nickserv_account=?', [session_id, nick])


def get_account_sesssions(db, account):
    result = db.execute('SELECT session_id FROM c3schedule_subscriptions WHERE nickserv_account=?', [account])
    if result is None:
        return []

    return [r[0] for r in result.fetchall()]


def setup(bot):
    logger.info('Setup')
    bot.config.define_section('c3schedule', ScheduleConfigSection)

    bot.memory['c3schedule'] = None
    bot.memory['c3schedule_current_tracks'] = {}
    bot.memory['c3schedule_angels'] = {}
    bot.memory['c3schedule_questions'] = {}

    # FIXME: remove this after initial development phase (pre 33c3)
    #bot.memory['c3schedule_fake_date'] = parse_date('2016-12-27')

    setup_database(bot.db)

    refresh_schedule(bot, startup=True)

def require_account(message=None):
    """
    Requires a valid account of the user triggering the command
    :param message:
    :return:
    """

    def actual_decorator(function):
        @functools.wraps(function)
        def guarded(bot, trigger, *args, **kwargs):
            if not trigger.account:
                if message and not callable(message):
                    bot.say(message)
            else:
                return function(bot, trigger, *args, **kwargs)

        return guarded

    return actual_decorator


def get_now(bot):
    now = pendulum.now('Europe/Berlin')
    if 'c3schedule_fake_date' in bot.memory:
        date = bot.memory['c3schedule_fake_date']
        return now.replace(year=date.year, month=date.month, day=date.day)

    return now


def get_today(bot):
    return bot.memory.get('c3schedule_fake_date', pendulum.now('Europe/Berlin').date())


@sopel.module.commands('help')
@sopel.module.require_privmsg()
@sopel.module.rate(user=10)
def show_help(bot, trigger):
    bot.say(
        "I'm here to help you attend the sessions you want to attend. You can ask me to remind you about upcoming sessions and changes to those.")
    bot.say("I understand the following commands:")
    bot.say(
        sopel.formatting.CONTROL_BOLD + ".info <id>" + sopel.formatting.CONTROL_NORMAL + " ‒ Get information (including the URL to the Fahrplan) for a session")
    bot.say(
        sopel.formatting.CONTROL_BOLD + ".subscribe <id>" + sopel.formatting.CONTROL_NORMAL + " ‒ Subscribe to a session. This will enable notifications. (Reminders, Changes)")
    bot.say(
        sopel.formatting.CONTROL_BOLD + '.unsubscribe <id>' + sopel.formatting.CONTROL_NORMAL + " ‒ Unsubscribe from a session. Using ALL as id will remove all sessions.")
    bot.say(
        sopel.formatting.CONTROL_BOLD + '.schedule' + sopel.formatting.CONTROL_NORMAL + " ‒ View your personal (upcoming) schedule."
    )
    bot.say(sopel.formatting.CONTROL_BOLD + '.search' + sopel.formatting.CONTROL_NORMAL + " ‒ Search for a session")
    bot.say(sopel.formatting.CONTROL_BOLD + '.nextup' + sopel.formatting.CONTROL_NORMAL + " ‒ See what is coming up")


@sopel.module.commands('search')
@sopel.module.require_privmsg()
@require_account(message='You can only view your personal schedule with a nickserv account')
@sopel.module.rate(user=1)
def search_session(bot, trigger):
    search_string = trigger.group(3)

    if search_string is None:
        bot.say('Usage: .search <term>')
        bot.say('If you include the string `do_not_record` it will match talks that have that flag set.')
        return

    schedule = bot.memory['c3schedule']
    RESULT_LIMIT = 10

    sessions = schedule.search_sessions(search_string, max_results=RESULT_LIMIT)

    if len(sessions) == 0:
        bot.say("No results found.")
        return

    bot.say('Here are the results (max {}):'.format(RESULT_LIMIT))
    for session in sessions:
        bot.say(session.format_summary())


@sopel.module.commands('nextup')
@sopel.module.require_privmsg()
@sopel.module.rate(user=10)
def show_nextup(bot, trigger):
    schedule = bot.memory['c3schedule']

    now = get_now(bot)
    sessions = [session for session in schedule.isessions() if session.date >= now]
    next_sessions = sorted(sessions, key=lambda session: session.date)[:6]

    if len(next_sessions) == 0:
        bot.say('Sorry but thats it. No more sessions :(')
    else:
        bot.say('Here is what is coming up next:')
        for session in next_sessions:
            bot.say(session.format_summary())


@sopel.module.commands('schedule')
@sopel.module.require_privmsg()
@require_account(message='You can only view your personal schedule with a nickserv account')
@sopel.module.rate(user=10)
def show_personal_schedule(bot, trigger):
    session_ids = get_account_sesssions(bot.db, trigger.account)

    if not session_ids:
        bot.say('You are not subscribed to any sessions yet.')
        return

    # resolve sessions to objects
    schedule = bot.memory['c3schedule']
    sessions = schedule.get_sessions(session_ids)

    now = get_now(bot)
    sessions = [session for session in sessions if session.date >= now or session.date + session.duration >= now]

    bot.say('Your personal (upcoming) schedule:')

    for session in sessions:
        bot.say(session.format_summary())


@sopel.module.commands('list')
@sopel.module.require_privmsg()
@sopel.module.rate(user=10)
@require_account(
    message='You can only view your personal list of subscriptions while being authenticated with nickserv')
def show_subscription_list(bot, trigger):
    session_ids = get_account_sesssions(bot.db, trigger.account)

    if len(session_ids) == 0:
        bot.say('You do not have any subscriptions.')
        return

    # resolve sessions to objects
    schedule = bot.memory['c3schedule']
    sessions = schedule.get_sessions(session_ids)

    bot.say('Your subscriptions:')
    for session in sessions:
        bot.say(session.format_summary())


@sopel.module.commands('info')
@sopel.module.require_privmsg()
@sopel.module.rate(user=3)
def show_info(bot, trigger):
    try:
        session_id = int(trigger.group(3))
    except (IndexError, TypeError):
        bot.say('Usage: .info <id>')
    else:
        session = bot.memory['c3schedule'].get_session(session_id)
        if session is None:
            bot.say('Sorry I could not find a session with that id')
            return

        bot.say(session.format_summary())

        if session.subtitle != '' or session.abstract != '':
            bot.say('\t{subtitle} ‒ {abstract}'.format(
                subtitle=session.subtitle, abstract=session.abstract),
                max_messages=2)
        bot.say('More in the Fahrplan at <' + session.url(bot) + '>')


@sopel.module.commands('subscribe')
@sopel.module.require_privmsg()
@require_account(message='You can only subscribe with a valid nickserv account')
@sopel.module.rate(user=0)
def subscribe_to_session(bot, trigger):
    try:
        session_id = int(trigger.group(3))
    except (IndexError, ValueError):
        bot.say('Usage: .subscribe <id>')
    else:
        session = bot.memory['c3schedule'].get_session(session_id)

        if session is None:
            bot.say('Sorry I could not find a session with that id')
            return

        session_ids = get_account_sesssions(bot.db, trigger.account)

        if session_id in session_ids:
            bot.say('You are already subscribed to that session')
            return

        add_nick_to_session_id(bot.db, trigger.account, session.id)
        bot.say('You are now subscribed to {} ({})'.format(session.title, session.id))
        if session.date < get_now(bot):
            bot.say(
                'The session is in the past. You might not get any notifications about this one. Check the fahrplan at {}'.format(
                    session.url(bot)))


@sopel.module.commands('unsubscribe')
@sopel.module.require_privmsg()
@require_account(message='You can only unsubscribe with a valid nickserv account')
@sopel.module.rate(user=1)
def unsubscribe_from_session(bot, trigger):
    try:
        session_id = trigger.group(3).lower()
        if session_id != 'all':
            session_id = int(session_id)
    except (IndexError, ValueError, TypeError):
        bot.say('Usage: .unsubscribe <id>')
    else:
        sessions = get_account_sesssions(bot.db, trigger.account)

        if session_id == 'all':
            for session_id in sessions:
                del_nick_from_session_id(bot.db, trigger.account, session_id)

            bot.say('I unsubscribed you from all sessions')
        else:
            if session_id not in sessions:
                bot.say('You are not subscribed to {}'.format(session_id))
                return

            del_nick_from_session_id(bot.db, trigger.account, session_id)

            bot.say('You are now unsubscribed from {}.'.format(session_id))


@sopel.module.commands('update')
@sopel.module.require_admin('You must be an admin for this command')
def trigger_update(bot, trigger):
    refresh_schedule(bot)
    update_topic(bot)


@sopel.module.commands('fakedate')
@sopel.module.require_admin('You must be an admin for this command')
def set_fake_date(bot, trigger):
    try:
        date = trigger.group(3)
    except IndexError:
        bot.reply('Usage: .fakedate 2042-02-03')
    else:

        if date.lower() == 'none':
            del bot.memory['c3schedule_fake_date']
            bot.say('Remove fake date.')
            return
        else:
            try:
                date = parse_date(date)
            except:
                bot.say('Failed to parse date. Format should be 2042-02-03')
            else:
                bot.memory['c3schedule_fake_date'] = date
                bot.say('Fake date set to %s' % date)


def set_topic(bot, channel, topic):
    bot.write(('TOPIC', channel + ' :' + topic))


def hall_channel_from_str(arg):
    lower_hall_channels = dict((k.lower(), v) for (k,v) in hall_channels.items())

    if not arg or (arg.lower() not in lower_hall_channels and arg not in hall_channels.values()):
        return None

    channel = lower_hall_channels[arg.lower()] if arg.lower() in lower_hall_channels else arg

    return channel

@sopel.module.commands('question')
@sopel.module.require_chanmsg()
@sopel.module.rate(user=2)
def ask_question(bot, trigger):
    channel = trigger.sender

    if channel in hall_channels.keys():
        logger.info("Got question outside of hall channel. Ignoring.")
        bot.reply('You can only ask questions in hall channels.')
        return

    question = trigger.group(3)
    if question is None or question == "":
        bot.reply('Usage: .question <question>')
        return

    questions = bot.memory['c3schedule_questions'].get(channel, [])
    questions += [ (trigger.nick, question) ]
    bot.memory['c3schedule_questions'][channel] = questions
    bot.reply('Question noted')


@sopel.module.commands('questions')
@sopel.module.require_privmsg()
def list_questions(bot, trigger):
    channel = trigger.group(3)

    if channel is None:
        bot.reply('Usage: .questions <#channel>')
        return

    channel = hall_channel_from_str(channel)

    if channel is None:
        bot.reply('Unknown hall channel')
        return

    questions = bot.memory['c3schedule_questions'].get(channel, [])
    for (i, (user, question)) in enumerate(questions):
        bot.reply('[{i}] {user} — {question}'.format(i=i, user=user, question=question))
    bot.reply('End of list ({count} questions)'.format(count=len(questions)))


@sopel.module.commands('clearquestions')
@sopel.module.require_privmsg()
def clear_questions(bot, trigger):
    channel = trigger.sender

    q_channel = trigger.group(3)

    if q_channel is None:
        bot.reply('Usage: .clearquestions <#channel>')
        return

    channel = hall_channel_from_str(q_channel)

    if channel is None:
        bot.reply('Unknown hall channel')
        return

    is_current_sa = bot.memory['c3schedule_angels'].get(channel) == trigger.nick
    is_some_sa = trigger.nick in bot.privileges[bot.config.c3schedule.angel_channel] and \
            bot.privileges[bot.config.c3schedule.angel_channel][trigger.nick] >= sopel.module.OP

    if not trigger.admin and bot.privileges[channel][trigger.nick] < sopel.module.OP and not is_current_sa and not is_some_sa:
        return


    if channel in bot.memory['c3schedule_questions']:
        bot.memory['c3schedule_questions'] = []

    bot.reply('Questions cleared')


def get_topic(bot, channel):
    lowercase_channel_topics = dict((k.lower(), v.topic) for (k, v) in bot.channels.items())
    return lowercase_channel_topics[channel]


@sopel.module.commands('sa')
@sopel.module.require_chanmsg()
def become(bot, trigger):
    channel = trigger.sender
    if not trigger.admin and bot.privileges[channel][trigger.nick] < sopel.module.OP:
        return

    if channel != bot.config.c3schedule.angel_channel:
        return

    arg = trigger.group(2)
    channel = hall_channel_from_str(arg)

    if channel is None:
        bot.reply('Unknown hall channel')
        return

    old_nick = bot.memory['c3schedule_angels'].get(channel)
    bot.memory['c3schedule_angels'][channel] = trigger.nick

    if not old_nick:
        old_nick = parse_signal_angel(bot, channel)

    bot.write(['MODE', channel, '+o', trigger.nick])

    if old_nick:
        old_topic = get_topic(bot, channel)
        topic = old_topic.replace('SA: {}'.format(old_nick),
                                  'SA: {}'.format(trigger.nick))
        set_topic(bot, channel, topic)
        bot.reply('topic updated in {}'.format(channel))
        #bot.write(['MODE', channel, '-o', old_nick])


    else:
        bot.reply('Topic does not have the | SA: <nick> | pattern?')


def get_conference_day(bot):
    schedule = bot.memory['c3schedule']
    if schedule is None:
        logger.info("No schedule known yet.")
        return

    today = get_today(bot)

    # determine current date of the conference
    if today < schedule.conference.start:
        # time difference in days:
        difference = -(schedule.conference.start - today).days
        difference += 1  # to adjust starting with 1 instead of 0 (day 0 is the day before)
        dayN = difference
    else:
        dayN = (today - schedule.conference.start).days + 1

    return dayN


def announce_scheduled_start(bot, session):
    diff = session.date - get_now(bot)
    pdiff = pendulum.interval.instance(diff)

    seconds = pdiff.total_seconds()

    color = None

    if seconds >= 800:
        color = sopel.formatting.colors.YELLOW
    elif seconds >= 500:
        color = sopel.formatting.colors.ORANGE
    else:
        color = sopel.formatting.colors.RED

    msg = session.format_short(
        color=color) + ' in ' + sopel.formatting.CONTROL_BOLD + pdiff.in_words() + sopel.formatting.CONTROL_NORMAL

    bot.msg(bot.config.c3schedule.channel, msg)

    if session.room in hall_channels:
        channel = hall_channels[session.room]
        signal_angel = bot.memory['c3schedule_angels'].get(channel)
        if not signal_angel:
            signal_angel = parse_signal_angel(bot, channel)

        topic = session.format_channel_topic(bot, angel=signal_angel)
        bot.msg(channel, msg)
        set_topic(bot, channel, topic)
    else:
        logger.info('%s (%s) not in hall_channels', session.room, type(session.room))

    for account in get_accounts_for_session_id(bot.db, session.id):
        for nick in get_nicks_for_account(bot, account):
            bot.msg(nick, msg)


def parse_signal_angel(bot, channel):
    topic = bot.channels[channel].topic

    parts = [ x.strip() for x in topic.split('|') ]
    for part in parts:
        if part.startswith('SA: '):
            logger.info('part: %s', part)
            return part[4:]

    return False


def announce_start(bot, session):
    msg = 'NOW ' + session.format_short(color=sopel.formatting.colors.RED)

    bot.msg(bot.config.c3schedule.channel, msg)

    if session.room in hall_channels:
        bot.msg(hall_channels[session.room], msg)
    else:
        logger.info('%s (%s) not in hall_channels', session.room, type(session.room))

    for account in get_accounts_for_session_id(bot.db, session.id):
        for nick in get_nicks_for_account(bot, account):
            bot.msg(nick, msg)


@sopel.module.interval(15)
@sopel.module.unblockable
def update_topic(bot):
    dayN = get_conference_day(bot)

    if dayN is None:
        return

    schedule = bot.memory['c3schedule']

    topic = render_jinja(bot.config.c3schedule.topic_template,
        acronym=schedule.conference.acronym, title=schedule.conference.title, start=schedule.conference.start,
        end=schedule.conference.end,
        dayN=dayN,
        url=render_jinja(bot.config.c3schedule.fahrplan_url, year=schedule.conference.start.year)
    )

    try:
        current_topic = bot.channels[bot.config.c3schedule.channel].topic
    except KeyError:
        logger.info("topic of %s not yet known. Retrying in 15s.", bot.config.c3schedule.channel)
    else:
        if current_topic != topic:
            set_topic(bot, bot.config.c3schedule.channel, topic)


def diff_schedules(old_schedule, schedule):
    changed_sessions, added_sessions, missing_sessions = [], [], []

    old_sessions = dict((s.id, s) for s in old_schedule.isessions())
    sessions = dict((s.id, s) for s in schedule.isessions())

    for session_id, session in old_sessions.items():
        if session_id not in sessions:
            missing_sessions.append(session)


    for session_id, session in sessions.items():
        if session_id not in old_sessions:
            added_sessions.append(session)

    for session_id, session in sessions.items():
        old_session = old_sessions.get(session_id)
        if old_session:
            if old_session != session:
                changed_sessions.append(session)

    return changed_sessions, added_sessions, missing_sessions



@sopel.module.interval(600)
@sopel.module.unblockable
def refresh_schedule(bot, startup=False):
    old_schedule = bot.memory['c3schedule']
    old_hashsum = bot.memory.get('c3hashsum')

    logger.info('Downloading schedule')
    task = ScheduleDownloadTask(render_jinja(bot.config.c3schedule.url, year=get_today(bot).year))
    hashsum, schedule = task.run()

    announcer = bot.memory.get('c3schedule_announcer')

    if announcer:
        announcer.stop()

    if old_schedule and schedule:
        if old_schedule.version != schedule.version or hashsum != old_hashsum:
            changed_sessions, added_sessions, missing_sessions = diff_schedules(old_schedule, schedule)

            # notify subscribers about changes to their tracks
            #for session in changed_sessions:
            #    send_session_changed(bot, bot.config.c3schedule.channel, session)
            #    for account in get_accounts_for_session_id(bot.db, session.id):
            #        send_session_changed_to_account(bot, account, session)

            for session in missing_sessions:
                send_session_removed(bot, bot.config.c3schedule.channel, session)
                for account in get_accounts_for_session_id(bot.db, session.id):
                    send_session_removed_to_account(bot, account, session)

            if not startup:
                for session in added_sessions:
                    send_session_added(bot, bot.config.c3schedule.channel, session)
                    for account in get_accounts_for_session_id(bot.db, session.id):
                        send_session_added_to_account(bot, account, session)

    if schedule is None:
        schedule = old_schedule

    bot.memory['c3schedule'] = schedule
    bot.memory['c3hashsum'] = hashsum

    if schedule:
        announcer = bot.memory['c3schedule_announcer'] = AnnoucementScheduler(bot)
        # try to schedule all sessions within the next hour seconds
        future = get_now(bot) + datetime.timedelta(hours=1)
        for day in schedule.conference.days:
            for room_name, room in day.rooms.items():
                for session in room.sessions.values():
                    if session.date < future:
                        announcer.add(session)


def get_nicks_for_account(bot, account):
    for user in bot.users.values():
        if user.account == account:
            yield user.nick


def send_session_changed_to_account(bot, account, session):
    for nick in get_nicks_for_account(bot, account):
        send_session_changed(bot, nick, session)


def send_session_changed(bot, to, session):
    title = session.title
    id = session.id
    url = session.url(bot)

    bot.msg(to, 'The session \'{title}\' ({id}) has been changed. Please check the website for details: {url}'.format(
        title=title, id=id, url=url
    ))


def send_session_removed_to_account(bot, account, session):
    for nick in get_nicks_for_account(bot, account):
        send_session_removed(bot, nick, session)


def send_session_removed(bot, to, session):
    bot.msg(to,
            'The session \'{title}\' ({id}) has been removed. In case it re-appears you\'ll be subscribed again.'.format(
                title=session.title, id=session.id
            ))


def send_session_added_to_account(bot, account, session):
    for nick in get_nicks_for_account(bot, account):
        send_session_added(bot, nick, session)


def send_session_added(bot, to, session):
    bot.msg(to,
            'The session \'{title}\' ({id}) has been added. You receive this notification since you might have subscribed to this session in the past.'.format(
                title=session.title, id=session.id
            ))


def parse_date(s):
    return pendulum.Date.instance(datetime.datetime.strptime(s, '%Y-%m-%d').date())


def parse_duration(s):
    parts = s.split(':')
    days = 0
    if len(parts) == 2:
       hours, minutes = parts
    elif len(parts) == 3:
       days, hours, minutes = parts

    return pendulum.Interval.instance(datetime.timedelta(days=int(days), hours=int(hours), minutes=int(minutes)))


def parse_day(s):
    return pendulum.Pendulum.instance(dateutil.parser.parse(s))


class Person:
    def __init__(self, id, full_public_name):
        self.id = id
        self.public_name = full_public_name

    def __eq__(self, other):
        return self.public_name == other.public_name

    @classmethod
    def from_json(cls, person_json):
        return Person(person_json['id'], person_json.get('full_public_name', person_json.get('public_name', person_json.get('name', 'N/A'))))


class Session:
    def __init__(self, id, guid, logo, date, start, duration, room, slug, title, subtitle, track, type, language,
                 abstract, description, recording_license, do_not_record, persons, links, attachments):
        self.id = id
        self.guid = guid
        self.logo = logo
        self.date = date
        self.start = start
        self.duration = duration
        self.room = room
        self.slug = slug
        self.title = title
        self.subtitle = subtitle
        self.track = track
        self.type = type
        self.language = language
        self.abstract = abstract
        self.description = description
        self.recording_license = recording_license
        self.do_not_record = do_not_record
        self.persons = persons
        self.links = links
        self.attachments = attachments

    def __eq__(self, other):

        for key, value in self.__dict__.items():
            if value != getattr(other, key):
                return False

        return True
        #
        # return self.id == other.id and self.guid == other.guid and self.logo == other.logo and self.date == other.date and self.start == other.start \
        #        and self.duration == other.duration and self.room == other.room and self.slug == other.slug and self.title == other.title \
        #        and self.subtitle == other.subtitle and self.track == other.track and self.type == other.type and self.language == other.language \
        #        and self.abstract == other.abstract and self.description == other.description and self.recording_license == other.recording_license \
        #        and self.do_not_record == other.do_not_record and self.persons == other.persons
        #
        #        #and self.links == other.links and self.attachments == other.attachments

    @classmethod
    def from_json(cls, session_json):
        return cls(session_json['id'],
                   session_json['guid'],
                   session_json['logo'],
                   parse_day(session_json['date']),
                   parse_duration(session_json['start']),
                   parse_duration(session_json['duration']),
                   session_json['room'],
                   session_json['slug'],
                   session_json['title'],
                   session_json['subtitle'],
                   session_json['track'],
                   session_json['type'],
                   session_json['language'],
                   session_json['abstract'],
                   session_json['description'],
                   session_json.get('recording_license', ''),
                   session_json.get('do_not_record', False),
                   [Person.from_json(p) for p in session_json['persons']],
                   session_json.get('links', []),
                   session_json.get('attachments', []))

    def _format_track(self):
        if self.track:
            track = '/{}'.format(self.track)
        else:
            track = ''

        return track


    def format_summary(self, color=None):
        date = str(self.date)
        if color:
            date = sopel.formatting.color(date, fg=color)

        title = self.title


        return '[{room}] {date} ({duration}) ‒ [{language}/{type}{track}] {bold}{title}{normal} / {persons} ({id})'.format(
            language=self.language,
            type=self.type,
            room=self.room,
            date=date,
            title=title,
            duration=self.duration,
            track=self._format_track(),
            persons=', '.join([p.public_name for p in self.persons]),
            bold=sopel.formatting.CONTROL_BOLD,
            normal=sopel.formatting.CONTROL_NORMAL,
            id=self.id
        )



    def format_channel_topic(self, bot, angel=None):
        template = bot.config.c3schedule.channel_topic_template
        suffix_template = bot.config.c3schedule.channel_topic_suffix
        stream_url_template = bot.config.c3schedule.stream_url_template
        stream_hall = {
#                'Curie': 'tent-1',
#                'Meitner': 'tent-2',
                "Ada": "halla",
                "Borg": "hallb",
                "Clarke": "hallc",
                "Dijkstra": "halld",
                "Eliza": "halle",
                "Chaos-West Bühne": "chaoswest",
                'OIO Stage': 'oio',
        }.get(self.room, self.room)
        logger.info('Room: %s Stream Hall: %s', self.room, stream_hall)
        kwargs = dict(session=self, angel=angel, bot=bot, stream_hall=stream_hall)
        stream_url = render_jinja(stream_url_template, **kwargs)
        kwargs['stream_url'] = stream_url
        suffix = render_jinja(suffix_template, **kwargs)
        topic = render_jinja(template,
            channel_topic_suffix=suffix,
            **kwargs
        )
        return topic

    def format_short(self, color=None):
        hour = '{:02}:{:02}'.format(self.date.hour, self.date.minute)
        if color:
            hour = sopel.formatting.color(hour, fg=color)

        title = self.title

        return '[{room}] {hour} ({duration}) - [{language}/{type}{track}] {bold}{title}{normal} / {persons} ({id})'.format(
            language=self.language,
            type=self.type,
            room=self.room,
            hour=hour,
            title=title,
            duration=self.duration,
            track=self._format_track(),
            persons=', '.join([p.public_name for p in self.persons]),
            bold=sopel.formatting.CONTROL_BOLD,
            normal=sopel.formatting.CONTROL_NORMAL,
            id=self.id
        )

    def url(self, bot):
        if self.track != 'self organized sessions':
            return render_jinja(bot.config.c3schedule.session_url, year=self.date.year, id=self.id, type=self.type, title=self.title, slug=self.slug, links=self.links)
        else:
            if len(self.links) == 0:
                return 'N/A'
            return ' '.join(self.links)


class Room:
    def __init__(self, name, sessions):
        self.name = name
        self.sessions = sessions

    @classmethod
    def from_json(cls, name, room_json):
        return cls(name, dict((session['id'], Session.from_json(session)) for session in room_json))


class Day:
    def __init__(self, index, date, day_start, day_end, rooms):
        self.index = index
        self.date = date
        self.day_start = day_start
        self.day_end = day_end
        self.rooms = rooms

    @classmethod
    def from_json(cls, day_json):
        return cls(day_json['index'],
                   parse_date(day_json['date']),
                   parse_day(day_json['day_start']),
                   parse_day(day_json['day_end']),
                   dict((name, Room.from_json(name, room)) for name, room in day_json['rooms'].items())
                   )


class Conference:
    def __init__(self, acronym, title, start, end, daysCount, timeslot_duration, days):
        self.acronym = acronym
        self.title = title
        self.start = start
        self.end = end
        self.daysCount = daysCount
        self.timelsot_duration = timeslot_duration
        self.days = days

    @classmethod
    def from_json(cls, conference_json):
        return cls(conference_json['acronym'],
                   conference_json['title'],
                   parse_date(conference_json['start']),
                   parse_date(conference_json['end']),
                   conference_json['daysCount'],
                   parse_duration(conference_json['timeslot_duration']),
                   [Day.from_json(day) for day in conference_json['days']]
                   )


class Schedule:
    def __init__(self, version, conference):
        self.version = version
        self.conference = conference

        self._session_by_id = {}

        self._hash_sessions()

    def _hash_sessions(self):
        self._session_by_id = {}

        for session in self.isessions():
            self._session_by_id[session.id] = session

    def get_session(self, session_id):
        return self._session_by_id.get(session_id)

    def get_sessions(self, session_ids):
        l = []
        for session_id in session_ids:
            s = self.get_session(session_id)
            if s:
                l.append(s)

        return sorted(l, key=lambda session: session.date)

    def isessions(self):
        for day in self.conference.days:
            for room_name, room in day.rooms.items():
                for session_id, session in room.sessions.items():
                    yield session

    def search_sessions(self, search_string, max_results=10):
        search_string = search_string.lower()
        sessions = []
        do_not_record = False

        def to_str(s):
            if s:
                return str(s)
            return ""

        if 'do_not_record' in search_string:
            do_not_record = True
            search_string = search_string.replace('do_not_record', '')

        for session in self.isessions():
            if search_string in to_str(session.title).lower() or \
                            search_string in to_str(session.description).lower() or \
                            search_string in to_str(session.abstract).lower() or \
                    any(search_string in to_str(p.public_name).lower() for p in session.persons) or \
                    do_not_record and session.do_not_record:
                sessions.append(session)

                if len(sessions) >= max_results:
                    break

        return sessions

    @classmethod
    def from_json(cls, schedule_json):
        conference = Conference.from_json(schedule_json['conference'])
        return cls(schedule_json.get('version', 'none'), conference)


class ScheduleDownloadTask:
    def __init__(self, url):
        self.url = url

    def run(self):
        try:
            response = requests.get(self.url)
        except requests.ConnectionError as e:
            logger.exception(e)
            return None
        else:
            try:
                hashsum = hashlib.md5(response.content).hexdigest()
                response_json = response.json()
                schedule_json = response_json['schedule']
            except Exception as e:
                logger.exception(e)
                return None
            else:
                try:
                    return hashsum, Schedule.from_json(schedule_json)
                except (KeyError, IndexError) as e:
                    logger.exception(e)
                    return None


class ScheduledSession:
    def __init__(self, scheduled_start_timer, start_timer):
        self.scheduled_start_timer = scheduled_start_timer
        self.start_timer = start_timer

    def stop(self):
        if self.start_timer and not self.start_timer.finished.is_set():
            logger.debug('stopping start_timer')
            self.start_timer.cancel()

        if self.scheduled_start_timer and not self.scheduled_start_timer.finished.is_set():
            logger.debug('stopping scheduled_start_timer')
            self.scheduled_start_timer.cancel()

    def start(self):
        if self.start_timer:
            self.start_timer.start()
        if self.scheduled_start_timer:
            self.scheduled_start_timer.start()

    def finished(self):
        return False  # FIXME: lets just stop the timers again..
        return self.start_timer.finished.is_set() and self.scheduled_start_timer.finished.is_set()


class AnnoucementScheduler:
    NOTIFICATION_DELTA = datetime.timedelta(minutes=15)

    def __init__(self, bot, timers=None):
        self.bot = bot
        self.timers = {} if timers is None else timers

    def stop(self):
        logger.info('Stopping scheduled announcements')
        for timer in self.timers.values():
            if not timer.finished():
                timer.stop()

        self.timers = {}

    def announce_start(self, session):
        announce_start(self.bot, session)

    def announce_scheduled_start(self, session):
        announce_scheduled_start(self.bot, session)

    def add(self, session):

        if session.id in self.timers:
            return

        now = get_now(self.bot)

        if session.date < now:
            return

        # calculate time till NOTIFICATION_DELTA is reached
        notification_time = session.date - self.NOTIFICATION_DELTA
        if notification_time < now:
            notification_time = now

        delay = (notification_time - now).total_seconds()
        if delay < 0:
            delay = 0

        announce_delay = (session.date - now).total_seconds()

        if delay > 0:
            scheduled_timer = threading.Timer(delay, self.announce_scheduled_start, (session,))
        else:
            scheduled_timer = None

        start_timer = threading.Timer(announce_delay, self.announce_start, (session,))

        ss = ScheduledSession(scheduled_start_timer=scheduled_timer, start_timer=start_timer)
        self.timers[session.id] = ss
        ss.start()
        logger.info(
            'Scheduled announcers for session.id {}. Start annoucement in {}. Announce delay: {}'.format(session.id,
                                                                                                         delay,
                                                                                                         announce_delay))
