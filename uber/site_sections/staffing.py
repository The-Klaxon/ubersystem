import cherrypy
from datetime import datetime, timedelta
from pockets.autolog import log
import ics

from uber.config import c
from uber.custom_tags import safe_string
from uber.decorators import ajax, ajax_gettable, all_renderable, check_shutdown, csrf_protected, render, public
from uber.errors import HTTPRedirect
from uber.models import Attendee
from uber.utils import check_csrf, create_valid_user_supplied_redirect_url, ensure_csrf_token_exists, localized_now


@all_renderable()
class Root:
    def index(self, session, message=''):
        if c.UBER_SHUT_DOWN:
            return render('staffing/printable.html', {'attendee': session.logged_in_volunteer()})
        else:
            return {
                'message': message,
                'attendee': session.logged_in_volunteer()
            }

    def printable(self, session):
        return {'attendee': session.logged_in_volunteer()}

    def food_restrictions(self, session, message='', **params):
        from uber.models.attendee import FoodRestrictions
        attendee = session.logged_in_volunteer()
        fr = attendee.food_restrictions or FoodRestrictions()
        if params:
            fr = session.food_restrictions(dict(params, attendee_id=attendee.id), checkgroups=['standard'])
            session.add(fr)
            if attendee.badge_type == c.GUEST_BADGE:
                raise HTTPRedirect('food_restrictions?message={}', 'Your info has been recorded, thanks a bunch!')
            else:
                raise HTTPRedirect('index?message={}', 'Your dietary restrictions have been recorded')

        return {
            'fr': fr,
            'message': message,
            'attendee': attendee
        }

    @check_shutdown
    def shirt_size(self, session, message='', **params):
        attendee = session.logged_in_volunteer()
        if cherrypy.request.method == "POST":
            check_csrf(params.get('csrf_token'))
            test_attendee = Attendee(**attendee.to_dict())
            test_attendee.apply(params)

            if c.STAFF_EVENT_SHIRT_OPTS and test_attendee.gets_staff_shirt and test_attendee.num_event_shirts == -1:
                message = "Please indicate your preference for shirt type."
            elif not test_attendee.shirt_size_marked:
                message = "Please select a shirt size."

            if not message:
                for attr in ['shirt', 'staff_shirt', 'num_event_shirts', 'shirt_opt_out']:
                    if params.get(attr):
                        setattr(attendee, attr, int(params.get(attr)))
                raise HTTPRedirect('index?message={}', 'Shirt info uploaded.')

        return {
            'message': message,
            'attendee': attendee,
            'opts': [('', 'Enter your shirt size')] + c.SHIRT_OPTS[1:]
        }

    @check_shutdown
    def volunteer_agreement(self, session, message='', agreed_to_terms=None, agreed_to_terms_1=None,
                            agreed_to_terms_2=None, csrf_token=None):
        attendee = session.logged_in_volunteer()
        if csrf_token is not None:
            check_csrf(csrf_token)
            if agreed_to_terms or (agreed_to_terms_1 and agreed_to_terms_2):
                attendee.agreed_to_volunteer_agreement = True
                raise HTTPRedirect('index?message={}', 'Agreement received')
            elif agreed_to_terms_1 or agreed_to_terms_2:
                message = "You must agree to both the terms of the agreement and "\
                    "the volunteering policies and guidelines"
            else:
                message = "You must agree to the terms of the agreement"

        return {
            'message': message,
            'attendee': attendee,
            'agreed_to_terms_1': agreed_to_terms_1,
            'agreed_to_terms_2': agreed_to_terms_2,
            'agreement_end_date': c.ESCHATON.date() + timedelta(days=31),
        }

    @check_shutdown
    def emergency_procedures(self, session, message='', reviewed_procedures=None, csrf_token=None):
        attendee = session.logged_in_volunteer()
        if csrf_token is not None:
            check_csrf(csrf_token)
            if reviewed_procedures:
                attendee.reviewed_emergency_procedures = True
                raise HTTPRedirect('index?message={}', 'Thanks for reviewing our emergency procedures!')

            message = "You must acknowledge that you reviewed our emerency procedures"

        return {
            'message': message,
            'attendee': attendee,
            'agreement_end_date': c.ESCHATON.date() + timedelta(days=31),
        }

    @check_shutdown
    def credits(self, session, message='', name_in_credits='', csrf_token=None):
        attendee = session.logged_in_volunteer()
        if csrf_token is not None:
            check_csrf(csrf_token)
            attendee.name_in_credits = name_in_credits
            message = "Thank you for providing a name for the credits roll!" if name_in_credits \
                else "You have opted out of having your name in the credits roll."
            raise HTTPRedirect('index?message={}', message)

        return {
            'message': message,
            'attendee': attendee,
        }

    @check_shutdown
    @public
    def volunteer(self, session, id, csrf_token=None, requested_depts_ids=None, message=''):
        attendee = session.attendee(id)
        if requested_depts_ids:
            check_csrf(csrf_token)
            attendee.staffing = True
            attendee.requested_depts_ids = requested_depts_ids
            raise HTTPRedirect(
                'login?message={}',
                "Thanks for signing up as a volunteer; you'll be emailed as "
                "soon as you are assigned to one or more departments.")

        return {
            'message': message,
            'attendee': attendee,
            'requested_depts_ids': requested_depts_ids
        }

    @check_shutdown
    def hotel(self, session, message='', decline=None, **params):
        if c.AFTER_ROOM_DEADLINE and not c.HAS_HOTEL_ADMIN_ACCESS:
            raise HTTPRedirect('../staffing/index?message={}', 'The room deadline has passed')
        attendee = session.logged_in_volunteer()
        if not attendee.hotel_eligible:
            raise HTTPRedirect('../staffing/index?message={}', 'You have not been marked as eligible for hotel space')
        requests = session.hotel_requests(params, checkgroups=['nights'], restricted=True)
        if 'attendee_id' in params:
            requests.attendee = attendee  # foreign keys are automatically admin-only
            session.add(requests)
            if decline or not requests.nights:
                requests.nights = ''
                raise HTTPRedirect(
                    '../staffing/index?message={}', "We've recorded that you've declined hotel room space")
            else:
                if requests.setup_teardown:
                    days = ' / '.join(
                        c.NIGHTS[day] for day in sorted(requests.nights_ints, key=c.NIGHT_DISPLAY_ORDER.index)
                        if day not in c.CORE_NIGHTS)

                    message = "Your hotel room request has been submitted. " \
                        "We'll let you know whether your offer to help on {} is accepted, " \
                        "and who your roommates will be, a few weeks after the deadline.".format(days)

                else:
                    message = "You've accepted hotel room space for {}. " \
                        "We'll let you know your roommates a few weeks after the " \
                        "deadline.".format(requests.nights_display)

                raise HTTPRedirect('../staffing/index?message={}', message)
        else:
            requests = attendee.hotel_requests or requests
            if requests.is_new:
                requests.nights = ','.join(map(str, c.CORE_NIGHTS))

        nights = []
        two_day_before = (c.EPOCH - timedelta(days=2)).strftime('%A')
        day_before = (c.EPOCH - timedelta(days=1)).strftime('%A')
        last_day = c.ESCHATON.strftime('%A').upper()
        day_after = (c.ESCHATON + timedelta(days=1)).strftime('%A')
        nights.append([getattr(c, two_day_before.upper()), getattr(requests, two_day_before.upper()),
                       "I'd like to help set up on " + two_day_before])
        nights.append([getattr(c, day_before.upper()), getattr(requests, day_before.upper()),
                       "I'd like to help set up on " + day_before])
        for night in c.CORE_NIGHTS:
            nights.append([night, night in requests.nights_ints, c.NIGHTS[night]])
        nights.append([getattr(c, last_day), getattr(requests, last_day),
                       "I'd like to help tear down on {} / {}".format(c.ESCHATON.strftime('%A'), day_after)])

        return {
            'nights':   nights,
            'message':  message,
            'requests': requests,
            'attendee': attendee
        }

    @check_shutdown
    def shifts(self, session, view='', start='', all=''):
        joblist = session.jobs_for_signups(all=all)
        con_days = -(-c.CON_LENGTH // 24)  # Equivalent to ceil(c.CON_LENGTH / 24)

        volunteer = session.logged_in_volunteer()
        assigned_dept_ids = set(volunteer.assigned_depts_ids)
        has_public_jobs = False
        for job in joblist:
            job['is_public_to_volunteer'] = job['is_public'] and job['department_id'] not in assigned_dept_ids
            if job['is_public_to_volunteer']:
                has_public_jobs = True

        has_setup = volunteer.can_work_setup or any(d.is_setup_approval_exempt for d in volunteer.assigned_depts)
        has_teardown = volunteer.can_work_teardown or any(
            d.is_teardown_approval_exempt for d in volunteer.assigned_depts)

        if not start and has_setup:
            start = c.SETUP_JOB_START
        elif not start:
            start = c.EPOCH
        else:
            if start.endswith('Z'):
                start = datetime.strptime(start[:-1], '%Y-%m-%dT%H:%M:%S.%f')
            else:
                start = datetime.strptime(start, '%Y-%m-%dT%H:%M:%S.%f')

        if has_setup and has_teardown:
            cal_length = c.CON_TOTAL_DAYS
        elif has_setup:
            cal_length = con_days + c.SETUP_SHIFT_DAYS
        elif has_teardown:
            cal_length = con_days + 2  # There's no specific config for # of shift signup days
        else:
            cal_length = con_days

        return {
            'jobs': joblist,
            'has_public_jobs': has_public_jobs,
            'depts_with_roles': [membership.department.name for membership in volunteer.dept_memberships_with_role],
            'name': volunteer.full_name,
            'hours': volunteer.weighted_hours,
            'assigned_depts_labels': volunteer.assigned_depts_labels,
            'view': view,
            'start': start,
            'end': start + timedelta(days=cal_length),
            'start_day': c.SHIFTS_START_DAY if has_setup else c.EPOCH,
            'show_all': all,
        }

    def shifts_ical(self, session, **params):
        attendee = session.logged_in_volunteer()
        icalendar = ics.Calendar()

        calname = "".join(filter(str.isalnum, attendee.full_name)) + "_Shifts"

        for shift in attendee.shifts:
            icalendar.events.add(ics.Event(
                name=shift.job.name,
                location=shift.job.department_name,
                begin=shift.job.start_time,
                end=(shift.job.start_time + timedelta(minutes=shift.job.duration)),
                description=shift.job.description))

        cherrypy.response.headers['Content-Type'] = \
            'text/calendar; charset=utf-8'
        cherrypy.response.headers['Content-Disposition'] = \
            'attachment; filename="{}.ics"'.format(calname)

        return icalendar

    @check_shutdown
    @ajax_gettable
    def jobs(self, session, all=False):
        return {'jobs': session.jobs_for_signups(all=all)}

    @check_shutdown
    @ajax
    def sign_up(self, session, job_id, all=False):
        return {
            'error': session.assign(session.logged_in_volunteer().id, job_id),
            'jobs': session.jobs_for_signups(all=all)
        }

    @check_shutdown
    @ajax
    def drop(self, session, job_id, all=False):
        if c.AFTER_DROP_SHIFTS_DEADLINE:
            return {
                'error': "You can no longer drop shifts.",
                'jobs': session.jobs_for_signups(all=all)
            }
        try:
            shift = session.shift(job_id=job_id, attendee_id=session.logged_in_volunteer().id)
            session.delete(shift)
            session.commit()
        except Exception:
            pass
        finally:
            return {'jobs': session.jobs_for_signups(all=all)}

    @public
    def login(self, session, message='', first_name='', last_name='', email='', zip_code='', original_location=None):
        original_location = create_valid_user_supplied_redirect_url(original_location, default_url='/staffing/index')

        if first_name or last_name or email or zip_code:
            try:
                attendee = session.lookup_attendee(first_name.strip(), last_name.strip(), email, zip_code)
                if not attendee.staffing:
                    message = safe_string(
                        'You are not signed up as a volunteer. '
                        '<a href="volunteer?id={}">Click Here</a> to sign up.'.format(attendee.id))
                elif not attendee.dept_memberships and not c.AT_THE_CON:
                    message = 'You have not been assigned to any departments; ' \
                        'an admin must assign you to a department before you can log in'
            except Exception:
                message = 'No attendee matches that name and email address and zip code'

            if not message:
                ensure_csrf_token_exists()
                cherrypy.session['staffer_id'] = attendee.id
                raise HTTPRedirect(original_location)

        return {
            'message':   message,
            'first_name': first_name,
            'last_name': last_name,
            'email': email,
            'zip_code':  zip_code,
            'original_location': original_location
        }

    def onsite_jobs(self, session, message=''):
        attendee = session.logged_in_volunteer()
        return {
            'message': message,
            'attendee': attendee,
            'jobs': [job for job in attendee.possible_and_current
                     if getattr(job, 'taken', False) or job.start_time > localized_now()]
        }

    @csrf_protected
    def onsite_sign_up(self, session, job_id):
        message = session.assign(session.logged_in_volunteer().id, job_id)
        raise HTTPRedirect('onsite_jobs?message={}', message or 'It worked')
