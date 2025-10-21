from flask import Flask, request, render_template, jsonify, redirect, session, url_for, flash
from geopy.geocoders import Nominatim
import logging
import json
import os
import time
import math
from retrying import retry
from pymongo import MongoClient
from pymongo.errors import ConnectionFailure, DuplicateKeyError
from bson import ObjectId
from werkzeug.security import generate_password_hash, check_password_hash
from datetime import datetime, timezone
from dotenv import load_dotenv
from pathlib import Path
from functools import partial
import requests
from requests.exceptions import RequestException

try:
    from zoneinfo import ZoneInfo
except ImportError:  # Python <3.9
    ZoneInfo = None

# Load environment variables from .env in the project root and current working directory
load_dotenv()
project_env = Path(__file__).resolve().parent / ".env"
if project_env.exists():
    load_dotenv(dotenv_path=project_env, override=False)

app = Flask(__name__)

# Set up logging
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

app.secret_key = os.getenv('FLASK_SECRET_KEY')
if not app.secret_key:
    app.secret_key = os.urandom(32)
    logger.warning("FLASK_SECRET_KEY not set; generated a temporary key for this run.")

# Configure display timezone (default Asia/Kolkata)
DISPLAY_TIMEZONE_NAME = os.getenv('DISPLAY_TIMEZONE', 'Asia/Kolkata').strip()
if ZoneInfo:
    try:
        DISPLAY_TIMEZONE = ZoneInfo(DISPLAY_TIMEZONE_NAME) if DISPLAY_TIMEZONE_NAME else None
    except Exception:
        logger = logging.getLogger(__name__)
        logger.warning("Invalid DISPLAY_TIMEZONE %s; defaulting to UTC display.", DISPLAY_TIMEZONE_NAME)
        DISPLAY_TIMEZONE = None
else:
    DISPLAY_TIMEZONE = None

# Jinja filter to render Unix timestamps in templates
@app.template_filter('timestamp_to_datetime')
def timestamp_to_datetime_filter(timestamp):
    try:
        if isinstance(timestamp, (int, float)):
            if DISPLAY_TIMEZONE:
                dt = datetime.fromtimestamp(timestamp, DISPLAY_TIMEZONE)
            else:
                dt = datetime.fromtimestamp(timestamp, timezone.utc).astimezone() if timezone.utc else datetime.fromtimestamp(timestamp)
            return dt.strftime('%Y-%m-%d %H:%M')
        # If it is already a datetime-like string, return as is
        return str(timestamp)
    except Exception:
        return ''

# MongoDB setup
try:
    mongo_uri = os.getenv('MONGODB_URI')
    if not mongo_uri:
        logger.warning("MONGODB_URI not set; defaulting to mongodb://localhost:27017/raithunestham")
        mongo_uri = "mongodb://localhost:27017/raithunestham"
    mongo_client = MongoClient(mongo_uri)
    db_name = os.getenv('MONGODB_DB', 'raithunestham')
    db = mongo_client[db_name]
    workers_collection = db['workers']  # Workers (laborers)
    tasks_collection = db['tasks']     # Jobs
    farmers_collection = db['farmers'] # Farmers (task posters)
    volunteers_collection = db['volunteers']  # Volunteers
    requests_collection = db['requests']  # New collection for help requests
    notifications_collection = db['notifications']  # New collection for notifications
    tasks_collection.create_index([("coordinates", "2dsphere")])
    workers_collection.create_index([("coordinates", "2dsphere")])
    # Create unique index to prevent duplicate tasks from same volunteer
    try:
        tasks_collection.create_index([("location", 1), ("task", 1), ("posted_by", 1), ("status", 1)], unique=True, partialFilterExpression={"status": "open"})
    except Exception as e:
        logger.warning(f"Could not create unique index (may already exist): {e}")
    
    cleanup_duplicates = os.getenv('ENABLE_DUPLICATE_TASK_CLEANUP', '').lower() in ('1', 'true', 'yes')
    if cleanup_duplicates:
        try:
            all_tasks = list(tasks_collection.find({"status": "open"}))
            task_groups = {}
            for task in all_tasks:
                key = f"{task['location']}_{task['task']}_{task['posted_by']}"
                task_groups.setdefault(key, []).append(task)

            removed_count = 0
            for key, grouped_tasks in task_groups.items():
                if len(grouped_tasks) > 1:
                    grouped_tasks.sort(key=lambda x: x.get('created_at', 0), reverse=True)
                    for duplicate_task in grouped_tasks[1:]:
                        tasks_collection.delete_one({"_id": duplicate_task["_id"]})
                        removed_count += 1
                        logger.info(f"Removed duplicate task: {duplicate_task['task']} at {duplicate_task['location']}")

            if removed_count > 0:
                logger.info(f"Cleaned up {removed_count} duplicate tasks")
        except Exception as e:
            logger.warning(f"Duplicate task cleanup failed: {e}")
    else:
        # Log duplicates without deleting when cleanup is disabled
        try:
            pipeline = [
                {"$match": {"status": "open"}},
                {"$group": {
                    "_id": {"location": "$location", "task": "$task", "posted_by": "$posted_by"},
                    "count": {"$sum": 1}
                }},
                {"$match": {"count": {"$gt": 1}}}
            ]
            duplicates = list(tasks_collection.aggregate(pipeline))
            for duplicate in duplicates:
                meta = duplicate["_id"]
                logger.warning(
                    "Duplicate open tasks detected for location=%s, task=%s, poster=%s. Cleanup disabled by default.",
                    meta.get("location"),
                    meta.get("task"),
                    meta.get("posted_by")
                )
        except Exception as e:
            logger.warning(f"Failed to identify duplicate tasks: {e}")
    
    # Check and update workers without phone numbers
    try:
        workers_without_phone = workers_collection.find({"phone": {"$exists": False}})
        updated_count = 0
        for worker in workers_without_phone:
            workers_collection.update_one(
                {"_id": worker["_id"]},
                {"$set": {"phone": "Phone not provided"}}
            )
            updated_count += 1
            logger.info(f"Updated worker {worker.get('name')} with default phone")
        
        if updated_count > 0:
            logger.info(f"Updated {updated_count} workers with default phone numbers")
    except Exception as e:
        logger.warning(f"Could not update worker phone numbers: {e}")
    
    logger.info("Connected to MongoDB with geospatial indexes")
except ConnectionFailure as e:
    logger.error(f"Failed to connect to MongoDB: {e}")
    raise

SMS_DEFAULT_COUNTRY_CODE = os.getenv('SMS_DEFAULT_COUNTRY_CODE', '+91') or '+91'
if not SMS_DEFAULT_COUNTRY_CODE.startswith('+'):
    SMS_DEFAULT_COUNTRY_CODE = '+' + SMS_DEFAULT_COUNTRY_CODE.lstrip('+')
try:
    WORKER_SMS_RADIUS_KM = float(os.getenv('WORKER_SMS_RADIUS_KM', '30'))
except ValueError:
    WORKER_SMS_RADIUS_KM = 30.0
try:
    WORKER_SMS_MAX_WORKERS = int(os.getenv('WORKER_SMS_MAX_WORKERS', '10'))
except ValueError:
    WORKER_SMS_MAX_WORKERS = 10

def normalize_phone_number(raw_number, default_country=SMS_DEFAULT_COUNTRY_CODE):
    if not raw_number:
        return None
    stripped = ''.join(ch for ch in str(raw_number) if ch.isdigit() or ch == '+')
    if not stripped:
        return None
    if stripped.startswith('+'):
        digits = ''.join(ch for ch in stripped[1:] if ch.isdigit())
        return f"+{digits}" if digits else None
    digits = ''.join(ch for ch in stripped if ch.isdigit())
    if not digits:
        return None
    country_digits = (default_country or '').lstrip('+')
    if country_digits and digits.startswith(country_digits):
        return f"+{digits}"
    trimmed = digits.lstrip('0') or digits
    if country_digits:
        return f"+{country_digits}{trimmed}"
    return f"+{trimmed}"

TWILIO_ACCOUNT_SID = os.getenv('TWILIO_ACCOUNT_SID')
TWILIO_AUTH_TOKEN = os.getenv('TWILIO_AUTH_TOKEN')
RAW_TWILIO_FROM_NUMBER = os.getenv('TWILIO_FROM_NUMBER')
TWILIO_FROM_NUMBER = normalize_phone_number(RAW_TWILIO_FROM_NUMBER, default_country=None) if RAW_TWILIO_FROM_NUMBER else None
TWILIO_AVAILABLE = all([TWILIO_ACCOUNT_SID, TWILIO_AUTH_TOKEN, TWILIO_FROM_NUMBER])
if not TWILIO_AVAILABLE:
    logger.info("Twilio credentials not fully configured; SMS notifications disabled.")

def send_sms_via_twilio(to_number, body):
    if not TWILIO_AVAILABLE:
        return False
    try:
        response = requests.post(
            f"https://api.twilio.com/2010-04-01/Accounts/{TWILIO_ACCOUNT_SID}/Messages.json",
            data={
                'To': to_number,
                'From': TWILIO_FROM_NUMBER,
                'Body': body
            },
            auth=(TWILIO_ACCOUNT_SID, TWILIO_AUTH_TOKEN),
            timeout=10
        )
        if response.status_code >= 400:
            logger.warning("Twilio SMS failed for %s: %s", to_number, response.text)
            return False
        logger.info("Twilio SMS sent to %s", to_number)
        return True
    except RequestException as exc:
        logger.error("Twilio SMS exception for %s: %s", to_number, exc)
        return False

def notify_nearby_workers_of_task(task_document, farmer_document=None):
    if not TWILIO_AVAILABLE:
        return
    coordinates_wrapper = task_document.get('coordinates') or {}
    coords = coordinates_wrapper.get('coordinates')
    if not (isinstance(coords, (list, tuple)) and len(coords) == 2):
        logger.debug("Skipping SMS notification; task coordinates unavailable for task %s", task_document.get('_id'))
        return
    farmer_phone_raw = (farmer_document or {}).get('phone')
    contact_number = normalize_phone_number(farmer_phone_raw)
    if not contact_number:
        logger.debug("Skipping SMS notification; farmer phone missing for task %s", task_document.get('_id'))
        return
    farmer_name = (farmer_document or {}).get('name', 'Farmer')
    query = {
        "coordinates": {
            "$near": {
                "$geometry": {"type": "Point", "coordinates": coords},
                "$maxDistance": int(WORKER_SMS_RADIUS_KM * 1000)
            }
        },
        "phone": {"$exists": True, "$ne": ""}
    }
    try:
        nearby_workers_cursor = workers_collection.find(query).limit(WORKER_SMS_MAX_WORKERS)
    except Exception as ge_err:
        logger.error("Error fetching nearby workers for SMS: %s", ge_err)
        return
    sent_numbers = set()
    sent_count = 0
    task_name = task_document.get('task', 'Farm job')
    location_label = task_document.get('location', 'nearby')
    wage_value = task_document.get('wage')
    wage_text = f" Wage ₹{wage_value}/day." if wage_value else ""
    for worker_record in nearby_workers_cursor:
        raw_phone = worker_record.get('phone', '')
        to_number = normalize_phone_number(raw_phone)
        if not to_number or to_number in sent_numbers or to_number == contact_number:
            continue
        message_body = (
            f"New job: {task_name} at {location_label}.{wage_text} "
            f"Contact {farmer_name} on {contact_number}."
        )
        if send_sms_via_twilio(to_number, message_body):
            sent_numbers.add(to_number)
            sent_count += 1
    if sent_count:
        logger.info(
            "SMS notifications sent for task %s to %s worker(s)",
            task_document.get('_id'),
            sent_count
        )
# Available tasks
task_labels = ["Sowing", "Weeding", "Harvesting", "Irrigation"]

FARMER_UI_LABELS = {
    "en": {
        "title": "Farmer Dashboard",
        "header_welcome": "Welcome back",
        "logout": "Logout",
        "flash_default": "Notice",
        "flash_levels": {
            "success": "Success",
            "info": "Info",
            "warning": "Warning",
            "error": "Error",
            "danger": "Error"
        },
        "post_kicker": "Create",
        "post_title": "Post New Task",
        "post_new_button": "New Task",
        "post_hide_button": "Hide Form",
        "post_help_text": "Share the work you need done and volunteers will make sure nearby workers are notified.",
        "form_mandal": "Mandal",
        "form_select_mandal": "Select Mandal",
        "form_village": "Village",
        "form_select_village": "Select Village",
        "form_task": "Task",
        "form_select_task": "Select Task",
        "form_num_workers": "Number of Workers",
        "form_wage": "Wage per day (₹)",
        "form_description": "Description (optional)",
        "form_description_placeholder": "Share field details, crop type or preferred timings.",
        "form_help_hint": "Need help from volunteers?",
        "form_submit": "Post Task",
        "summary_kicker": "Profile",
        "summary_title": "Farm Summary",
        "summary_village": "Village",
        "summary_phone": "Phone",
        "link_request_help": "Request assistance",
        "link_guide_worker": "Guide a worker",
        "open_tasks": "Open tasks",
        "active_kicker": "Tasks",
        "active_title": "Active Postings",
        "active_empty": "You have no active postings yet. Post a task to see it here.",
        "active_workers_label": "workers",
        "wage_suffix": "/day",
        "notifications_kicker": "Updates",
        "notifications_title": "Notifications",
        "notifications_empty": "No notifications yet. Worker updates will appear here.",
        "notification_location": "Location",
        "notification_worker_phone": "Worker phone",
        "notification_phone_missing": "Not provided",
        "notification_when": "When",
        "help_kicker": "Volunteer support",
        "help_title": "Help Request Status",
        "help_volunteer": "Volunteer:",
        "help_workers_needed": "Workers needed:",
        "help_accepted_header": "Accepted workers",
        "help_contact_hint": "Contact the workers directly or coordinate through the volunteer above.",
        "help_no_workers": "No workers have accepted yet. Your request is still visible to volunteers.",
        "help_empty": "You have not requested volunteer help yet.",
        "refresh_button": "Refresh",
        "footer_dashboard": "Dashboard",
        "footer_messages": "Messages",
        "footer_workers": "Workers",
        "footer_profile": "Profile"
    },
    "te": {
        "title": "రైతు డ్యాష్‌బోర్డ్",
        "header_welcome": "మళ్లీ స్వాగతం",
        "logout": "లాగ్ అవుట్",
        "flash_default": "సూచన",
        "flash_levels": {
            "success": "విజయం",
            "info": "సమాచారం",
            "warning": "హెచ్చరిక",
            "error": "లోపం",
            "danger": "లోపం"
        },
        "post_kicker": "సృష్టించండి",
        "post_title": "కొత్త పని పోస్ట్ చేయండి",
        "post_new_button": "కొత్త పని",
        "post_hide_button": "ఫారం దాచు",
        "post_help_text": "మీకు అవసరమైన పనిని పంచుకోండి; సేవకులు సమీప కార్మికులకు సమాచారాన్ని అందిస్తారు.",
        "form_mandal": "మండలం",
        "form_select_mandal": "మండలాన్ని ఎంచుకోండి",
        "form_village": "గ్రామం",
        "form_select_village": "గ్రామాన్ని ఎంచుకోండి",
        "form_task": "పని",
        "form_select_task": "పనిని ఎంచుకోండి",
        "form_num_workers": "కార్మికుల సంఖ్య",
        "form_wage": "రోజువారీ వేతనం (₹)",
        "form_description": "వివరణ (ఐచ్చికం)",
        "form_description_placeholder": "పంట, సమయంలో వివరాలు లేదా ఇతర సూచనలు నమోదు చేయండి.",
        "form_help_hint": "సేవకుల సహాయం కావాలా?",
        "form_submit": "పని పోస్ట్ చేయండి",
        "summary_kicker": "ప్రొఫైల్",
        "summary_title": "వ్యవసాయ సమగ్రం",
        "summary_village": "గ్రామం",
        "summary_phone": "ఫోన్",
        "link_request_help": "సహాయం కోరండి",
        "link_guide_worker": "కార్మికుడికి మార్గనిర్దేశం చేయండి",
        "open_tasks": "తెరిచి ఉన్న పనులు",
        "active_kicker": "పనులు",
        "active_title": "క్రియాశీల ప్రకటనలు",
        "active_empty": "మీరు ఇంకా ఏ పనినీ పోస్ట్ చేయలేదు. ఇక్కడ చూడటానికి పని పోస్ట్ చేయండి.",
        "active_workers_label": "కార్మికులు",
        "wage_suffix": "రోజుకు",
        "notifications_kicker": "నవీకరణలు",
        "notifications_title": "అధిసూచనలు",
        "notifications_empty": "ఇంకా అధిసూచనలు లేవు. కార్మికుల నవీకరణలు ఇక్కడ కనిపిస్తాయి.",
        "notification_location": "స్థానం",
        "notification_worker_phone": "కార్మికుని ఫోన్",
        "notification_phone_missing": "అందుబాటులో లేదు",
        "notification_when": "సమయం",
        "help_kicker": "సేవకుల సహాయం",
        "help_title": "సహాయ అభ్యర్థన స్థితి",
        "help_volunteer": "సేవకుడు:",
        "help_workers_needed": "అవసరమైన కార్మికులు:",
        "help_accepted_header": "అంగీకరించిన కార్మికులు",
        "help_contact_hint": "ఈ కార్మికులను నేరుగా లేదా పై సేవకుడి ద్వారా సంప్రదించండి.",
        "help_no_workers": "ఇంకా కార్మికులు అంగీకరించలేదు. మీ అభ్యర్థన ఇంకా సేవకులకు కనిపిస్తుంది.",
        "help_empty": "మీరు ఇప్పటివరకు సేవకుల సహాయం కోరలేదు.",
        "refresh_button": "రీఫ్రెష్",
        "footer_dashboard": "డ్యాష్‌బోర్డ్",
        "footer_messages": "సందేశాలు",
        "footer_workers": "కార్మికులు",
        "footer_profile": "ప్రొఫైల్"
    }
}

FARMER_STATUS_LABELS = {
    "en": {
        "open": "Open",
        "in_progress": "In Progress",
        "completed": "Completed",
        "filled": "Filled",
        "assigned": "Assigned",
        "pending": "Pending",
        "declined": "Declined",
        "accepted": "Accepted"
    },
    "te": {
        "open": "తెరిచి ఉంది",
        "in_progress": "పనిలో ఉంది",
        "completed": "పూర్తైంది",
        "filled": "పూర్తయింది",
        "assigned": "కేటాయించబడింది",
        "pending": "పెండింగ్",
        "declined": "తిరస్కరించబడింది",
        "accepted": "అంగీకరించబడింది"
    }
}

VOLUNTEER_STATUS_LABELS = {
    "en": {
        "open": "Open",
        "in_progress": "In Progress",
        "filled": "Filled",
        "completed": "Completed",
        "accepted": "Accepted",
        "declined": "Declined",
        "pending": "Pending"
    },
    "te": {
        "open": "తెరిచి ఉంది",
        "in_progress": "ప్రగతిలో",
        "filled": "పూర్తయింది",
        "completed": "పూర్తైంది",
        "accepted": "అంగీకరించబడింది",
        "declined": "తిరస్కరించబడింది",
        "pending": "పెండింగ్"
    }
}

FARMER_MESSAGES = {
    "en": {
        "missing_fields": "All required fields must be filled",
        "invalid_mandal": "Please select a valid mandal",
        "invalid_village": "Please select a valid village",
        "invalid_task": "Please select a valid task type",
        "negative_wage": "Wage cannot be negative",
        "invalid_workers_range": "Number of workers must be between 1 and 50",
        "no_coordinates": "Could not find coordinates for the specified village.",
        "duplicate_task": "A similar {task} task already exists for {location}",
        "post_success": "✅ Task '{task}' successfully posted for {location} with wage ₹{wage}{wage_suffix} for {num_workers} worker(s)",
        "post_failure": "Failed to post task. Please try again.",
        "invalid_format": "Invalid wage or number of workers format",
        "post_exception": "An error occurred: {error}"
    },
    "te": {
        "missing_fields": "అన్ని తప్పనిసరి వివరాలు నమోదు చేయాలి",
        "invalid_mandal": "దయచేసి సరైన మండలాన్ని ఎంచుకోండి",
        "invalid_village": "దయచేసి సరైన గ్రామాన్ని ఎంచుకోండి",
        "invalid_task": "దయచేసి సరైన పనిని ఎంచుకోండి",
        "negative_wage": "వేతనం ప్రతికూలంగా ఉండకూడదు",
        "invalid_workers_range": "కార్మికుల సంఖ్య 1 నుండి 50 మధ్యలో ఉండాలి",
        "no_coordinates": "ఎంచుకున్న గ్రామానికి స్థానాన్ని గుర్తించలేకపోయాం.",
        "duplicate_task": "అదే {location} కోసం '{task}' పని ఇప్పటికే ఉంది",
        "post_success": "✅ '{task}' పనిని {location} కోసం విజయవంతంగా పోస్ట్ చేశారు. వేతనం ₹{wage} {wage_suffix} {num_workers} కార్మికుల కోసం.",
        "post_failure": "పని పోస్ట్ చేయడంలో విఫలమైంది. దయచేసి మళ్లీ ప్రయత్నించండి.",
        "invalid_format": "వేతనం లేదా కార్మికుల సంఖ్య సరైన రూపంలో లేదు",
        "post_exception": "లోపం జరిగింది: {error}"
    }
}

def get_farmer_message(key: str, lang: str, **kwargs) -> str:
    lang = lang if lang in FARMER_MESSAGES else "en"
    template = FARMER_MESSAGES[lang].get(key) or FARMER_MESSAGES["en"].get(key, "")
    if template and kwargs:
        return template.format(**kwargs)
    return template

def translate_text(lang: str, english: str, telugu: str) -> str:
    """Return english or telugu text based on language preference."""
    if lang == "te":
        return telugu
    return english

def make_translator(lang: str):
    """Provide a helper callable for templates to pick the right translation."""
    normalized = lang if lang in ("en", "te") else "en"
    return partial(translate_text, normalized)

def normalize_object_id_str(value) -> str:
    """Normalize ObjectId-like values to a plain 24-character hex string."""
    if isinstance(value, ObjectId):
        return str(value)
    if not value:
        return ''
    value_str = str(value).strip()
    if ObjectId.is_valid(value_str):
        return value_str
    if value_str.startswith("ObjectId(") and value_str.endswith(")"):
        inner = value_str[9:-1].strip(" '\"")
        if ObjectId.is_valid(inner):
            return inner
    return value_str


@app.context_processor
def inject_translation_utilities():
    lang = session.get('preferred_lang', 'en').lower()
    if lang not in ('en', 'te'):
        lang = 'en'
    translator = make_translator(lang)
    flash_config = FARMER_UI_LABELS.get(lang, FARMER_UI_LABELS['en'])
    return {
        'translate': translator,
        'lang': lang,
        'flash_labels': flash_config['flash_levels'],
        'flash_default': flash_config['flash_default']
    }


def ensure_worker_assignment_entry(task_id: ObjectId, worker_id: str, defaults: dict | None = None) -> dict | None:
    """Ensure worker assignment subdocument exists for a task."""
    defaults = defaults or {}
    try:
        tasks_collection.update_one({"_id": task_id, "worker_assignments": {"$exists": False}}, {"$set": {"worker_assignments": []}})
        existing = tasks_collection.find_one({"_id": task_id, "worker_assignments.worker_id": worker_id}, {"worker_assignments.$": 1})
        if existing and existing.get('worker_assignments'):
            return existing['worker_assignments'][0]

        entry = {
            "worker_id": worker_id,
            "confirmed": defaults.get('confirmed', False),
            "arrived": defaults.get('arrived', False),
            "paid": defaults.get('paid', False),
            "completed": defaults.get('completed', False),
            "rating": defaults.get('rating'),
            "accepted_at": defaults.get('accepted_at', time.time()),
            "confirmed_at": defaults.get('confirmed_at'),
            "arrived_at": defaults.get('arrived_at'),
            "paid_at": defaults.get('paid_at'),
            "rated_at": defaults.get('rated_at')
        }
        tasks_collection.update_one({"_id": task_id}, {"$push": {"worker_assignments": entry}})
        return entry
    except Exception as err:
        logger.error(f"Failed to ensure worker assignment entry for task {task_id}: {err}")
        return None

def mark_task_accepted_by_worker(task_id: str, worker_id: str, worker_name: str | None = None, worker_phone: str | None = None) -> bool:
    """Mark an existing farmer task as accepted by a worker."""
    cleaned_task_id = normalize_object_id_str(task_id)
    cleaned_worker_id = normalize_object_id_str(worker_id)
    if not cleaned_task_id or not cleaned_worker_id:
        return False
    if not ObjectId.is_valid(cleaned_task_id):
        logger.warning("Task id %s is not a valid ObjectId; cannot mark acceptance.", task_id)
        return False
    task_object_id = ObjectId(cleaned_task_id)
    try:
        task_doc = tasks_collection.find_one({"_id": task_object_id})
    except Exception as err:
        logger.error("Failed to fetch task %s for worker acceptance: %s", task_id, err)
        return False
    if not task_doc:
        logger.warning("Task %s not found when attempting to mark worker acceptance.", task_id)
        return False

    accepted_workers = task_doc.get('accepted_workers') or []
    if not isinstance(accepted_workers, list):
        accepted_workers = list(accepted_workers)

    added_worker = cleaned_worker_id not in accepted_workers
    if added_worker:
        accepted_workers.append(cleaned_worker_id)

    ensure_worker_assignment_entry(task_object_id, cleaned_worker_id, {"accepted_at": time.time()})

    try:
        required = int(task_doc.get('num_workers', 1))
        if required < 1:
            required = 1
    except Exception:
        required = 1

    current_status = task_doc.get('status') or 'open'
    if len(accepted_workers) >= required:
        new_status = 'filled'
    elif current_status == 'open':
        new_status = 'in_progress'
    else:
        new_status = current_status

    update_fields = {"accepted_workers": accepted_workers}
    if new_status != current_status:
        update_fields["status"] = new_status

    update_ops = {"$set": update_fields, "$pull": {"denied_by": cleaned_worker_id}}
    try:
        tasks_collection.update_one({"_id": task_object_id}, update_ops)
    except Exception as err:
        logger.error("Failed to update task %s after worker acceptance: %s", task_id, err)
        return False

    if added_worker:
        final_worker_name = worker_name
        final_worker_phone = worker_phone
        if final_worker_name is None or final_worker_phone is None:
            try:
                worker_doc = workers_collection.find_one({"_id": ObjectId(cleaned_worker_id)})
            except Exception:
                worker_doc = None
            if worker_doc:
                if final_worker_name is None:
                    final_worker_name = worker_doc.get('name')
                if final_worker_phone is None:
                    final_worker_phone = worker_doc.get('phone')

        try:
            farmer_id_for_notification = None
            farmer_reference = task_doc.get('farmer_id')
            if farmer_reference:
                farmer_id_for_notification = normalize_object_id_str(farmer_reference)
            else:
                posted_by_candidate = normalize_object_id_str(task_doc.get('posted_by'))
                if posted_by_candidate and ObjectId.is_valid(posted_by_candidate):
                    farmer_lookup = farmers_collection.find_one({"_id": ObjectId(posted_by_candidate)})
                    if farmer_lookup:
                        farmer_id_for_notification = str(farmer_lookup['_id'])

            if farmer_id_for_notification:
                notifications_collection.insert_one({
                    "farmer_id": farmer_id_for_notification,
                    "worker_id": cleaned_worker_id,
                    "task_id": cleaned_task_id,
                    "help_request_id": task_doc.get('help_request_id'),
                    "message": f"Your task has been accepted by worker: {final_worker_name or 'Worker'}",
                    "worker_name": final_worker_name or 'Worker',
                    "worker_phone": final_worker_phone or '',
                    "task_location": task_doc.get('location'),
                    "created_at": time.time(),
                    "read": False
                })
        except Exception as err:
            logger.error("Failed to create farmer notification for worker acceptance on task %s: %s", task_id, err)

    logger.info("Marked task %s as accepted by worker %s via volunteer assistance.", cleaned_task_id, cleaned_worker_id)
    return True

# Haversine formula for distance calculation
def haversine_distance(lat1, lon1, lat2, lon2):
    lat1, lon1, lat2, lon2 = map(math.radians, [lat1, lon1, lat2, lon2])
    dlat = lat2 - lat1
    dlon = lon2 - lon1
    a = math.sin(dlat/2)**2 + math.cos(lat1) * math.cos(lat2) * math.sin(dlon/2)**2
    c = 2 * math.asin(math.sqrt(a))
    r = 6371
    return c * r

# Gudlavalleru mandal villages
villages = [
    "angaluru", "chandrala", "chinagonnuru", "chitram", "dokiparru", "gadepudi",
    "gudlavalleru", "kowtaram", "kurada", "mamidikolla", "nagavaram", "penjendra",
    "pesaramilli", "puritipadu", "seri kalavapudi", "seridaggumilli", "ulavalapudi",
    "vadlamannadu", "vemavaram", "vemavarappalem", "venuturumilli", "vinnakota"
]

# Fallback coordinates for villages
fallback_coordinates = {
    "angaluru": [81.0487, 16.3471],
    "chandrala": [81.0423, 16.3520],
    "chinagonnuru": [81.0550, 16.3400],
    "chitram": [81.0600, 16.3450],
    "dokiparru": [81.0380, 16.3600],
    "gadepudi": [81.0450, 16.3550],
    "gudlavalleru": [81.0500, 16.3500],
    "kowtaram": [81.0700, 16.3400],
    "kurada": [81.0650, 16.3450],
    "mamidikolla": [81.0400, 16.3650],
    "nagavaram": [81.0580, 16.3530],
    "penjendra": [81.0520, 16.3480],
    "pesaramilli": [81.0620, 16.3420],
    "puritipadu": [81.0460, 16.3570],
    "seri kalavapudi": [81.0680, 16.3470],
    "seridaggumilli": [81.0540, 16.3510],
    "ulavalapudi": [81.0560, 16.3490],
    "vadlamannadu": [81.0440, 16.3630],
    "vemavaram": [81.0660, 16.3460],
    "vemavarappalem": [81.0600, 16.3500],
    "venuturumilli": [81.0480, 16.3540],
    "vinnakota": [81.0640, 16.3440]
}

# Cache villages
village_cache = {"gudlavalleru": [v.lower() for v in villages]}
cache_file = "villages.json"

def save_village_cache():
    try:
        with open(cache_file, 'w') as f:
            json.dump(village_cache, f)
        logger.info("Saved village cache")
    except Exception as e:
        logger.error(f"Error saving village cache: {e}")

def load_village_cache():
    if os.path.exists(cache_file):
        try:
            with open(cache_file, 'r') as f:
                cached = json.load(f)
                village_cache.update({k.lower(): [v.lower() for v in villages] for k, villages in cached.items()})
            logger.info("Loaded village cache")
            return True
        except Exception as e:
            logger.error(f"Error loading village cache: {e}")
    return False

def populate_village_cache():
    start_time = time.time()
    if load_village_cache():
        logger.info("Village cache loaded from file")
        return
    village_cache["gudlavalleru"] = [v.lower() for v in villages]
    save_village_cache()
    logger.info(f"Village cache populated in {time.time() - start_time:.2f} seconds")


def create_worker_message(worker_id, worker_phone, volunteer_id, volunteer_phone, task_details, worker_name, volunteer_name):
    try:
        task_name = task_details.get('task_name') if task_details else ''
        task_location = task_details.get('task_location') if task_details else ''
        task_wage = task_details.get('task_wage') if task_details else ''
        message_en = (
            f"Hello {worker_name or 'worker'}, volunteer {volunteer_name} accepted your help request.\n"
            f"Task: {task_name or 'Not specified'}\n"
            f"Village: {task_location or 'Not specified'}\n"
            f"Wage: ₹{task_wage or '—'}/day\n"
            f"Call {volunteer_phone or 'volunteer'} to coordinate."
        )
        message_te = (
            f"హలో {worker_name or 'కార్మికుడు'}, సేవకుడు {volunteer_name or ''} మీ సహాయ అభ్యర్థనను అంగీకరించారు.\n"
            f"పని: {task_name or 'తెలియదు'}\n"
            f"గ్రామం: {task_location or 'తెలియదు'}\n"
            f"వేతనం: ₹{task_wage or '—'}/రోజు\n"
            f"సమన్వయానికి {volunteer_phone or 'సేవకుడు'} కు కాల్ చేయండి."
        )
        notifications_collection.insert_one({
            "worker_id": str(worker_id) if worker_id else None,
            "worker_phone": worker_phone,
            "volunteer_id": volunteer_id,
            "volunteer_phone": volunteer_phone,
            "task_id": task_details.get('task_id') if task_details else None,
            "message_en": message_en,
            "message_te": message_te,
            "role": "worker",
            "created_at": time.time(),
            "read": False
        })
    except Exception as err:
        logger.error(f"Failed to create worker message: {err}")

populate_village_cache()

geolocator = Nominatim(user_agent="agri_platform")

@retry(stop_max_attempt_number=3, wait_fixed=2000)
def get_coordinates(location, village=None):
    try:
        loc = geolocator.geocode(f"{village}, Gudlavalleru, Krishna, Andhra Pradesh, India", timeout=10)
        if loc:
            return [loc.longitude, loc.latitude]
    except Exception as e:
        logger.warning(f"Geocoding failed for {location}: {e}")
    if village and village.lower() in fallback_coordinates:
        return fallback_coordinates[village.lower()]
    return None

@app.route('/')
def index():
    logger.info("Rendering home page")
    return render_template('index.html')

@app.route('/get-started')
def get_started():
    lang = request.args.get('lang') or session.get('preferred_lang') or (request.accept_languages.best_match(['en', 'te']) or 'en')
    lang = lang.lower()
    session['preferred_lang'] = lang
    if lang == 'te':
        template = 'get_started_te.html'
    else:
        template = 'get_started_en.html'
    logger.info("Rendering get-started page for language: %s", lang)
    return render_template(template)

@app.route('/get_villages', methods=['GET'])
def get_villages():
    villages_list = village_cache.get("gudlavalleru", [])
    logger.info(f"Returning {len(villages_list)} villages for Gudlavalleru mandal")
    return jsonify(villages_list)

@app.route('/predict_task', methods=['GET'])
def predict_task():
    village = request.args.get('village', '').strip().lower()
    location = f"{village}, gudlavalleru"
    assigned = tasks_collection.find_one({"location": location})
    if assigned:
        logger.info(f"Found existing task for {location}: {assigned['task']}")
        return jsonify({"task": assigned["task"]})
    if not village:
        logger.info("Village not provided for prediction; returning default task")
        return jsonify({"task": task_labels[0]})

    # Simple deterministic heuristic: map village name hash to available tasks
    predicted_task_idx = abs(hash(village)) % len(task_labels)
    predicted_task = task_labels[predicted_task_idx]
    logger.info(f"Heuristic task suggestion for {location}: {predicted_task}")
    return jsonify({"task": predicted_task})

@app.route('/farmer_login', methods=['GET', 'POST'])
def farmer_login():
    lang = session.get('preferred_lang', 'en').lower()
    if request.method == 'POST':
        try:
            name = request.form.get('name', '').strip()
            password = request.form.get('password', '').strip()
           
            logger.info(f"Farmer login attempt for: {name}")
           
            if not name or not password:
                return render_template(
                    'farmer_login.html',
                    error=translate_text(lang, "Please enter both name and password", "దయచేసి పేరు మరియు సంకేతపదాన్ని నమోదు చేయండి."),
                    lang=lang
                )
           
            farmer = farmers_collection.find_one({"name": name})
           
            if not farmer:
                logger.warning(f"Farmer not found: {name}")
                return render_template(
                    'farmer_login.html',
                    error=translate_text(lang, "Farmer not found. Please check your name or register first.", "రైతు కనబడలేదు. మీ పేరు తనిఖీ చేయండి లేదా ముందుగా నమోదు చేసుకోండి."),
                    lang=lang
                )
           
            if not check_password_hash(farmer['password'], password):
                logger.warning(f"Invalid password for farmer: {name}")
                return render_template(
                    'farmer_login.html',
                    error=translate_text(lang, "Invalid password. Please try again.", "చెల్లని సంకేతపదం. దయచేసి మళ్లీ ప్రయత్నించండి."),
                    lang=lang
                )
           
            session['farmer_id'] = str(farmer['_id'])
            logger.info(f"Farmer {name} logged in successfully, redirecting to dashboard")
            flash(translate_text(lang, "Farmer login successful! Welcome to the dashboard.", "రైతు లాగిన్ విజయవంతం! డ్యాష్‌బోర్డ్‌కు స్వాగతం."), "success")
            return redirect('/farmer')
        except Exception as e:
            logger.error(f"Error in farmer login: {e}")
            return render_template(
                'farmer_login.html',
                error=translate_text(lang, f"Login error: {str(e)}", f"లాగిన్ లోపం: {str(e)}"),
                lang=lang
            )
   
    logger.info("Rendering farmer login page")
    return render_template('farmer_login.html', lang=lang)

@app.route('/farmer_reg', methods=['GET', 'POST'])
def farmer_reg():
    lang = session.get('preferred_lang', 'en').lower()
    if request.method == 'POST':
        name = request.form.get('name', '').strip()
        password = request.form.get('password', '').strip()
        phone = request.form.get('phone', '').strip()
        village = request.form.get('village', '').strip().lower()
        
        if not all([name, password, phone, village]):
            return render_template(
                'farmer_reg.html',
                error=translate_text(lang, "All fields required", "అన్ని ఫీల్డ్లు అవసరం"),
                villages=villages,
                lang=lang
            )
        if farmers_collection.find_one({"name": name}):
            return render_template(
                'farmer_reg.html',
                error=translate_text(lang, "Name already exists", "పేరు ఇప్పటికే ఉంది"),
                villages=villages,
                lang=lang
            )
        if village not in [v.lower() for v in villages]:
            return render_template(
                'farmer_reg.html',
                error=translate_text(lang, "Invalid village", "చెల్లని గ్రామం"),
                villages=villages,
                lang=lang
            )
        try:
            farmer_data = {
                "name": name,
                "password": generate_password_hash(password),
                "phone": phone,
                "village": village,
                "location": f"{village}, gudlavalleru"
            }
            farmers_collection.insert_one(farmer_data)
            logger.info(f"Farmer {name} registered successfully, redirecting to login")
            flash(translate_text(
                lang,
                "Farmer registration completed successfully! Please login.",
                "రైతు నమోదు విజయవంతమైంది! దయచేసి లాగిన్ చేయండి."
            ), "success")
            return redirect('/farmer_login')
        except Exception as e:
            logger.error(f"Error registering farmer {name}: {e}")
            return render_template(
                'farmer_reg.html',
                error=translate_text(lang, f"Registration failed: {str(e)}", f"నమోదు విఫలమైంది: {str(e)}"),
                villages=villages,
                lang=lang
            )
   
    logger.info("Rendering farmer registration page")
    return render_template('farmer_reg.html', villages=villages, lang=lang)

@app.route('/farmer', methods=['GET', 'POST'])
def farmer():
    if 'farmer_id' not in session:
        logger.warning("Unauthorized access to farmer dashboard - redirecting to login")
        return redirect('/farmer_login')
   
    farmer = farmers_collection.find_one({"_id": ObjectId(session['farmer_id'])})
    if not farmer:
        logger.warning("Farmer session invalid - redirecting to login")
        session.pop('farmer_id', None)
        return redirect('/farmer_login')
   
    message = None
    message_type = 'info'
    accepted_overview = None
    notifications = None
    my_open_tasks = []
    lang = session.get('preferred_lang', 'en').lower()
    if lang not in FARMER_UI_LABELS:
        lang = 'en'
    labels = FARMER_UI_LABELS[lang]
    status_labels = FARMER_STATUS_LABELS.get(lang, FARMER_STATUS_LABELS['en'])
   
    if request.method == 'POST':
        form_type = request.form.get('form_type')
        action = request.form.get('action')
        farmer_id = session.get('farmer_id')

        if action == 'clear_notifications':
            if farmer_id:
                try:
                    notifications_collection.delete_many({"farmer_id": farmer_id})
                except Exception as err:
                    logger.error(f"Error clearing notifications for farmer {farmer_id}: {err}")
                flash(translate_text(lang, "Notifications cleared.", "అధిసూచనలు తొలగించబడ్డాయి."), 'success')
            return redirect('/farmer')

        if form_type == 'assignment_update':
            task_id_raw = (request.form.get('task_id') or '').strip()
            worker_id = (request.form.get('worker_id') or '').strip()
            field = (request.form.get('field') or '').strip()
            valid_fields = {'confirmed', 'arrived', 'paid'}
            if not farmer_id or not task_id_raw or not worker_id or field not in valid_fields:
                flash(get_farmer_message("post_exception", lang, error="Invalid assignment update"), 'warning')
                return redirect('/farmer')
            try:
                task_object_id = ObjectId(task_id_raw)
            except Exception:
                flash(get_farmer_message("post_exception", lang, error="Invalid task"), 'danger')
                return redirect('/farmer')

            update_fields = {f"worker_assignments.$.{field}": True}
            timestamp_field = {
                'confirmed': 'confirmed_at',
                'arrived': 'arrived_at',
                'paid': 'paid_at'
            }.get(field)
            if timestamp_field:
                update_fields[f"worker_assignments.$.{timestamp_field}"] = time.time()

            ensure_worker_assignment_entry(task_object_id, worker_id)
            result = tasks_collection.update_one(
                {
                    "_id": task_object_id,
                    "worker_assignments.worker_id": worker_id,
                    "$or": [{"posted_by": farmer_id}, {"farmer_id": farmer_id}]
                },
                {"$set": update_fields}
            )
            if result.modified_count:
                success_messages = {
                    'confirmed': translate_text(lang, "Worker confirmed.", "కార్మికుడు ధృవీకరించబడింది."),
                    'arrived': translate_text(lang, "Arrival recorded.", "రాక నమోదు అయింది."),
                    'paid': translate_text(lang, "Marked as paid.", "చెల్లింపు నమోదైంది.")
                }
                flash(success_messages.get(field, translate_text(lang, "Updated assignment.", "అసైన్‌మెంట్ నవీకరించబడింది.")), 'success')
            else:
                flash(translate_text(lang, "Could not update assignment.", "అసైన్‌మెంట్‌ను నవీకరించలేకపోయాం."), 'warning')
            return redirect('/farmer')

        if form_type == 'delete_task':
            task_id_raw = (request.form.get('task_id') or '').strip()
            if not farmer_id or not task_id_raw:
                flash(translate_text(lang, "Unable to delete task.", "పనిని తొలగించలేకపోయాం."), 'warning')
                return redirect('/farmer')
            try:
                task_object_id = ObjectId(task_id_raw)
            except Exception:
                flash(translate_text(lang, "Invalid task selected.", "ఎంచుకున్న పని చెల్లుబాటు కావడం లేదు."), 'danger')
                return redirect('/farmer')

            task_filter = {
                "_id": task_object_id,
                "$or": [
                    {"posted_by": farmer_id},
                    {"farmer_id": farmer_id}
                ]
            }
            task_doc = tasks_collection.find_one(task_filter)
            if not task_doc:
                flash(translate_text(lang, "Task could not be found or belongs to another account.", "పని కనబడలేదు లేదా ఇతర ఖాతాకు చెందింది."), 'danger')
                return redirect('/farmer')

            try:
                tasks_collection.delete_one(task_filter)
                notifications_collection.delete_many({"task_id": str(task_object_id)})
                flash(translate_text(lang, "Task deleted successfully.", "పని విజయవంతంగా తొలగించబడింది."), 'success')
            except Exception as err:
                logger.error(f"Error deleting farmer task {task_id_raw}: {err}")
                flash(translate_text(lang, "Could not delete task. Please try again.", "పనిని తొలగించలేకపోయాం. మళ్లీ ప్రయత్నించండి."), 'danger')
            return redirect('/farmer')

        if form_type == 'assignment_rating':
            task_id_raw = (request.form.get('task_id') or '').strip()
            worker_id = (request.form.get('worker_id') or '').strip()
            rating_raw = (request.form.get('rating') or '').strip()
            if not farmer_id or not task_id_raw or not worker_id or not rating_raw:
                flash(translate_text(lang, "Rating submission incomplete.", "రేటింగ్ సమర్పణ అసంపూర్ణంగా ఉంది."), 'warning')
                return redirect('/farmer')
            try:
                rating_value = int(rating_raw)
            except ValueError:
                rating_value = 0
            if rating_value < 1 or rating_value > 5:
                flash(translate_text(lang, "Please select a rating between 1 and 5.", "దయచేసి 1 నుండి 5 మధ్య రేటింగ్ ఎంచుకోండి."), 'warning')
                return redirect('/farmer')
            try:
                task_object_id = ObjectId(task_id_raw)
            except Exception:
                flash(translate_text(lang, "Invalid task selected for rating.", "రేటింగ్ కోసం ఎంచుకున్న పని చెల్లుబాటు కావడం లేదు."), 'danger')
                return redirect('/farmer')

            task_doc = tasks_collection.find_one({"_id": task_object_id})
            if not task_doc:
                flash(translate_text(lang, "Task not found for rating.", "రేటింగ్ కోసం పని కనబడలేదు."), 'danger')
                return redirect('/farmer')

            assignment_entry = None
            for entry in task_doc.get('worker_assignments', []):
                if entry.get('worker_id') == worker_id:
                    assignment_entry = entry
                    break
            if not assignment_entry:
                assignment_entry = ensure_worker_assignment_entry(task_object_id, worker_id)
            if not assignment_entry:
                flash(translate_text(lang, "Worker not linked to this task.", "ఈ పనికి కార్మికుడు అనుసంధానించబడలేదు."), 'warning')
                return redirect('/farmer')
            if assignment_entry.get('rating'):
                flash(translate_text(lang, "You already rated this worker for this task.", "ఈ పనికి మీరు ఇప్పటికే కార్మికుడిని రేట్ చేశారు."), 'info')
                return redirect('/farmer')

            update_result = tasks_collection.update_one(
                {
                    "_id": task_object_id,
                    "worker_assignments.worker_id": worker_id,
                    "$or": [{"posted_by": farmer_id}, {"farmer_id": farmer_id}]
                },
                {
                    "$set": {
                        "worker_assignments.$.rating": rating_value,
                        "worker_assignments.$.rated_at": time.time()
                    }
                }
            )
            if update_result.modified_count:
                try:
                    worker_obj_id = ObjectId(worker_id)
                except Exception:
                    worker_obj_id = None
                if worker_obj_id:
                    workers_collection.update_one(
                        {"_id": worker_obj_id},
                        {
                            "$push": {"ratings": {"task_id": task_id_raw, "rating": rating_value, "rated_at": time.time()}},
                            "$inc": {"rating_total": rating_value, "rating_count": 1}
                        }
                    )
                flash(translate_text(lang, "Rating submitted.", "రేటింగ్ సమర్పించబడింది."), 'success')
            else:
                flash(translate_text(lang, "Could not submit rating.", "రేటింగ్ సమర్పించలేకపోయాం."), 'warning')
            return redirect('/farmer')

        process_task_form = not form_type and action not in ('fetch_help_status', 'clear_notifications')

        if process_task_form:
            mandal = request.form.get('mandal', '').strip().lower()
            village = request.form.get('village', '').strip().lower()
            task = request.form.get('task', '').strip()
            wage = request.form.get('wage', '').strip()
            num_workers = request.form.get('num_workers', '').strip()
            description = request.form.get('description', '').strip()

            logger.info(f"Farmer {farmer['name']} attempting to post task: {task} in {village}, {mandal}")

            if not all([mandal, village, task]):
                message = get_farmer_message("missing_fields", lang)
                message_type = 'error'
            elif mandal != 'gudlavalleru':
                message = get_farmer_message("invalid_mandal", lang)
                message_type = 'error'
            elif village not in [v.lower() for v in villages]:
                message = get_farmer_message("invalid_village", lang)
                message_type = 'error'
            elif task not in task_labels:
                message = get_farmer_message("invalid_task", lang)
                message_type = 'error'
            else:
                try:
                    wage = float(wage) if wage else 0.0
                    num_workers = int(num_workers) if num_workers else 1

                    if wage < 0:
                        message = get_farmer_message("negative_wage", lang)
                        message_type = 'error'
                    elif num_workers < 1 or num_workers > 50:
                        message = get_farmer_message("invalid_workers_range", lang)
                        message_type = 'error'
                    else:
                        location = f"{village}, {mandal}"
                        coordinates = get_coordinates(location, village)
                        location_label = f"{village.capitalize()}, {mandal.capitalize()}"

                        if not coordinates:
                            message = get_farmer_message("no_coordinates", lang)
                            message_type = 'error'
                        else:
                            task_data = {
                                "location": location,
                                "task": task,
                                "village": village.capitalize(),
                                "mandal": mandal.capitalize(),
                                "coordinates": {"type": "Point", "coordinates": coordinates},
                                "wage": wage,
                                "num_workers": num_workers,
                                "description": description,
                                "posted_by": session['farmer_id'],
                                "accepted_by": None,
                                "accepted_workers": [],
                                "worker_assignments": [],
                                "denied_by": [],
                                "created_at": time.time(),
                                "status": "open",
                                "completed": False,
                                "completed_at": None
                            }

                            existing_task = tasks_collection.find_one({
                                "location": location,
                                "task": task,
                                "posted_by": session['farmer_id'],
                                "status": "open"
                            })

                            if existing_task:
                                message = get_farmer_message("duplicate_task", lang, task=task, location=location_label)
                                message_type = 'warning'
                            else:
                                try:
                                    result = tasks_collection.insert_one(task_data)
                                except DuplicateKeyError:
                                    message = get_farmer_message("duplicate_task", lang, task=task, location=location_label)
                                    message_type = 'warning'
                                    logger.info(
                                        "Duplicate task prevented for farmer %s at %s (%s)",
                                        session['farmer_id'],
                                        location,
                                        task
                                    )
                                except Exception as insert_error:
                                    message = get_farmer_message("post_exception", lang, error=str(insert_error))
                                    message_type = 'error'
                                    logger.error(f"Error inserting farmer task: {insert_error}")
                                else:
                                    if result.inserted_id:
                                        message = get_farmer_message(
                                            "post_success",
                                            lang,
                                            task=task,
                                            location=location_label,
                                            wage=f"{wage:g}",
                                            num_workers=num_workers,
                                            wage_suffix=labels['wage_suffix']
                                        )
                                        message_type = 'success'
                                        logger.info(f"Task {task} posted successfully for {location} by farmer {session['farmer_id']}")
                                        task_snapshot = task_data.copy()
                                        task_snapshot['_id'] = result.inserted_id
                                        notify_nearby_workers_of_task(task_snapshot, farmer)
                                    else:
                                        message = get_farmer_message("post_failure", lang)
                                        message_type = 'error'
                except ValueError as e:
                    message = get_farmer_message("invalid_format", lang)
                    message_type = 'error'
                    logger.error(f"Value error in task posting: {e}")
                except Exception as e:
                    message = get_farmer_message("post_exception", lang, error=str(e))
                    message_type = 'error'
                    logger.error(f"Error in farmer task posting: {e}")

    # Fetch farmer's own open tasks for assignment UI
    try:
        farmer_id_value = session.get('farmer_id')
        my_open_filter = {
            "status": {"$in": ["open", "in_progress"]},
            "$or": [{"posted_by": farmer_id_value}]
        }
        if farmer_id_value:
            my_open_filter["$or"].append({"farmer_id": farmer_id_value})

        my_open_tasks = list(tasks_collection.find(my_open_filter).sort("created_at", -1))
    except Exception as e:
        logger.error(f"Error fetching farmer's open tasks: {e}")

    assignment_overview = []
    farmer_id_filter = session.get('farmer_id')
    if farmer_id_filter:
        try:
            assignment_filter = {
                "worker_assignments": {"$exists": True, "$ne": []},
                "$or": [
                    {"posted_by": farmer_id_filter},
                    {"farmer_id": farmer_id_filter}
                ]
            }
            assignment_tasks = tasks_collection.find(assignment_filter).sort("created_at", -1).limit(20)
            for t in assignment_tasks:
                entries = []
                assignments_raw = t.get('worker_assignments') or []
                if (not assignments_raw) and t.get('accepted_workers'):
                    assignments_raw = []
                    for wid in t.get('accepted_workers', []):
                        defaults = {'accepted_at': t.get('created_at')}
                        seeded = ensure_worker_assignment_entry(t['_id'], wid, defaults)
                        assignments_raw.append(seeded or {
                            "worker_id": wid,
                            "confirmed": False,
                            "arrived": False,
                            "paid": False,
                            "completed": False,
                            "rating": None
                        })

                for entry in assignments_raw:
                    if not entry:
                        continue
                    worker_doc = None
                    try:
                        worker_doc = workers_collection.find_one({"_id": ObjectId(entry.get('worker_id'))})
                    except Exception:
                        worker_doc = None
                    entries.append({
                        "worker_id": entry.get('worker_id'),
                        "name": worker_doc.get('name', 'Worker') if worker_doc else 'Worker',
                        "phone": worker_doc.get('phone') if worker_doc else '',
                        "village": worker_doc.get('village') if worker_doc else '',
                        "confirmed": entry.get('confirmed'),
                        "arrived": entry.get('arrived'),
                        "paid": entry.get('paid'),
                        "completed": entry.get('completed'),
                        "rating": entry.get('rating')
                    })

                volunteer_name = None
                poster_id = t.get('posted_by')
                if t.get('task_type') == 'help_request' and poster_id and poster_id != farmer_id_filter:
                    try:
                        volunteer_doc = volunteers_collection.find_one({"_id": ObjectId(poster_id)})
                        if volunteer_doc:
                            volunteer_name = volunteer_doc.get('name')
                    except Exception:
                        volunteer_name = None

                assignment_overview.append({
                    "task_id": str(t.get('_id')),
                    "task": t.get('task'),
                    "location": t.get('location'),
                    "wage": t.get('wage'),
                    "num_workers": t.get('num_workers'),
                    "status": t.get('status'),
                    "completed": t.get('completed'),
                    "volunteer_name": volunteer_name,
                    "assignments": entries
                })
        except Exception as err:
            logger.error(f"Error building assignment overview: {err}")

    accepted_overview = assignment_overview

    if notifications is None and farmer_id_filter:
        try:
            notifications = list(notifications_collection.find({"farmer_id": farmer_id_filter}).sort("created_at", -1).limit(5))
        except Exception as err:
            logger.error(f"Error fetching notifications for farmer {farmer_id_filter}: {err}")
            notifications = []
    notifications = notifications or []

    logger.info(f"Rendering farmer dashboard for {farmer['name']}")
    return render_template(
        'farmer.html',
        message=message,
        message_type=message_type,
        tasks=task_labels,
        villages=villages,
        farmer=farmer,
        accepted_overview=accepted_overview,
        notifications=notifications,
        my_open_tasks=my_open_tasks,
        labels=labels,
        status_labels=status_labels,
        lang=lang
    )

@app.route('/farmer_help', methods=['GET', 'POST'])
def farmer_help():
    lang = session.get('preferred_lang', 'en').lower()
    if lang not in ('en', 'te'):
        lang = 'en'
    translator = make_translator(lang)
    flash_config = FARMER_UI_LABELS.get(lang, FARMER_UI_LABELS['en'])
    flash_labels = flash_config['flash_levels']
    flash_default = flash_config['flash_default']

    farmer = None
    if 'farmer_id' in session:
        try:
            farmer = farmers_collection.find_one({"_id": ObjectId(session['farmer_id'])})
        except Exception:
            farmer = None

    volunteer_docs = list(volunteers_collection.find({}).sort("name", 1))
    volunteers_by_village = {}
    volunteer_options = []

    for vol in volunteer_docs:
        village_lower = (vol.get('village') or '').lower()
        entry = {
            "id": str(vol.get('_id')),
            "name": vol.get('name', 'Volunteer'),
            "phone": vol.get('phone', ''),
            "village": village_lower
        }
        volunteer_options.append(entry)
        if village_lower:
            volunteers_by_village.setdefault(village_lower, []).append(entry)

    volunteer_groups = []
    for v in villages:
        volunteer_groups.append({
            "label": v,
            "volunteers": volunteers_by_village.get(v.lower(), [])
        })

    if request.method == 'POST':
        if 'farmer_id' not in session:
            flash(translate_text(lang, "Please login as a farmer to send a request.", "దయచేసి రైతుగా లాగిన్ అయ్యి అభ్యర్థన పంపండి."), "warning")
            return redirect('/farmer_login')
        volunteer_id = (request.form.get('volunteer_id') or '').strip()
        if not volunteer_id:
            flash(translate_text(lang, "Please select a volunteer before submitting.", "దయచేసి సమర్పించడానికి ముందు ఒక సేవకుడిని ఎంచుకోండి."), "warning")
            return redirect('/farmer_help')
        request_data = {
            "farmer_id": session['farmer_id'],
            "volunteer_id": volunteer_id,
            "request_type": "farmer_help",
            "status": "pending",
            "created_at": time.time()
        }
        try:
            requests_collection.insert_one(request_data)
            flash(translate_text(lang, "Request sent to volunteer!", "సేవకునికి అభ్యర్థన పంపబడింది!"), "success")
        except Exception as err:
            logger.error(f"Error creating farmer help request: {err}")
            flash(translate_text(lang, "Could not send request. Please try again.", "అభ్యర్థన పంపడం సాధ్యపడలేదు. దయచేసి మళ్లీ ప్రయత్నించండి."), "danger")
            return redirect('/farmer_help')
        return redirect('/farmer')

    return render_template(
        'farmer_help.html',
        villages=villages,
        volunteer_groups=volunteer_groups,
        volunteer_options=volunteer_options,
        volunteers_by_village=volunteers_by_village,
        farmer=farmer,
        lang=lang,
        translate=translator,
        flash_labels=flash_labels,
        flash_default=flash_default
    )

@app.route('/worker_help', methods=['GET', 'POST'])
def worker_help():
    lang = session.get('preferred_lang', 'en').lower()
    if lang not in ('en', 'te'):
        lang = 'en'
    translator = make_translator(lang)
    flash_config = FARMER_UI_LABELS.get(lang, FARMER_UI_LABELS['en'])
    flash_labels = flash_config['flash_levels']
    flash_default = flash_config['flash_default']

    worker_doc = None
    if 'worker_id' in session:
        try:
            worker_doc = workers_collection.find_one({"_id": ObjectId(session['worker_id'])})
        except Exception:
            worker_doc = None

    all_volunteers = list(volunteers_collection.find({}).sort("name", 1))
    volunteers_by_village = {}
    volunteer_options = []

    prefill_village = request.args.get('village', '').strip().lower()
    prefill_task_name = request.args.get('task_name', '').strip()
    prefill_task_wage = request.args.get('task_wage', '').strip()
    prefill_task_location = request.args.get('task_location', '').strip()
    prefill_task_id = request.args.get('task_id', '').strip()

    for vol in all_volunteers:
        village = (vol.get('village') or '').lower()
        volunteer_entry = {
            "id": str(vol.get('_id')),
            "name": vol.get('name', 'Volunteer'),
            "phone": vol.get('phone', ''),
            "village": village
        }
        volunteer_options.append(volunteer_entry)
        if village:
            volunteers_by_village.setdefault(village, []).append(volunteer_entry)

    if request.method == 'POST':
        worker_name = request.form.get('worker_name', '').strip()
        worker_phone = request.form.get('worker_phone', '').strip()
        worker_village = request.form.get('worker_village', '').strip().lower()
        volunteer_id = request.form.get('volunteer_id', '').strip()
        worker_notes = request.form.get('worker_notes', '').strip()
        task_name = request.form.get('task_name', '').strip()
        task_wage = request.form.get('task_wage', '').strip()
        task_location = request.form.get('task_location', '').strip()
        task_id = request.form.get('task_id', '').strip()

        errors = []
        if not worker_name or not worker_phone:
            errors.append(translate_text(lang, "Please provide your name and phone number.", "దయచేసి మీ పేరు మరియు ఫోన్ నంబర్ నమోదు చేయండి."))
        if not volunteer_id:
            errors.append(translate_text(lang, "Please select a volunteer to contact.", "సంప్రదించడానికి ఒక సేవకుడిని ఎంచుకోండి."))
        if worker_village and worker_village not in [v.lower() for v in villages]:
            errors.append(translate_text(lang, "Please select a valid village.", "దయచేసి చెల్లుబాటు అయ్యే గ్రామాన్ని ఎంచుకోండి."))

        volunteer_doc = None
        if volunteer_id:
            try:
                volunteer_doc = volunteers_collection.find_one({"_id": ObjectId(volunteer_id)})
                if not volunteer_doc:
                    errors.append(translate_text(lang, "Selected volunteer was not found.", "ఎంచుకున్న సేవకుడు కనబడలేదు."))
            except Exception:
                errors.append(translate_text(lang, "Selected volunteer is invalid.", "ఎంచుకున్న సేవకుడు చెల్లుబాటు కాలేదు."))

        if errors:
            for err in errors:
                flash(err, "danger")
        else:
            request_data = {
                "request_type": "worker_help",
                "worker_id": session.get('worker_id'),
                "worker_name": worker_name,
                "worker_phone": worker_phone,
                "worker_village": worker_village if worker_village else None,
                "volunteer_id": volunteer_id,
                "status": "pending",
                "created_at": time.time()
            }
            if worker_notes:
                request_data["worker_notes"] = worker_notes
            if task_name or task_wage or task_location or task_id:
                request_data["task_details"] = {
                    "task_name": task_name,
                    "task_wage": task_wage,
                    "task_location": task_location,
                    "task_id": task_id
                }
            requests_collection.insert_one(request_data)
            flash(translate_text(lang, "Help request sent to volunteer! They will reach out to you soon.", "సహాయ అభ్యర్థన సేవకునికి పంపబడింది! వారు త్వరలో సంప్రదిస్తారు."), "success")
            return redirect('/worker_help')

    return render_template(
        'worker_help.html',
        villages=villages,
        volunteers_by_village=volunteers_by_village,
        volunteer_options=volunteer_options,
        prefill_village=prefill_village,
        prefill_task_name=prefill_task_name,
        prefill_task_wage=prefill_task_wage,
        prefill_task_location=prefill_task_location,
        prefill_task_id=prefill_task_id,
        lang=lang,
        translate=translator,
        flash_labels=flash_labels,
        flash_default=flash_default,
        worker=worker_doc
    )

@app.route('/volunteer_login', methods=['GET', 'POST'])
def volunteer_login():
    lang = session.get('preferred_lang', 'en').lower()
    if request.method == 'POST':
        try:
            name = request.form.get('name', '').strip()
            password = request.form.get('password', '').strip()
           
            logger.info(f"Volunteer login attempt for: {name}")
           
            if not name or not password:
                return render_template(
                    'volunteer_login.html',
                    error=translate_text(lang, "Please enter both name and password", "దయచేసి పేరు మరియు సంకేతపదాన్ని నమోదు చేయండి."),
                    lang=lang
                )
           
            volunteer = volunteers_collection.find_one({"name": name})
           
            if not volunteer:
                logger.warning(f"Volunteer not found: {name}")
                return render_template(
                    'volunteer_login.html',
                    error=translate_text(lang, "Volunteer not found. Please check your name or register first.", "సేవకుడు కనబడలేదు. మీ పేరు తనిఖీ చేయండి లేదా ముందుగా నమోదు చేసుకోండి."),
                    lang=lang
                )
           
            if not check_password_hash(volunteer['password'], password):
                logger.warning(f"Invalid password for volunteer: {name}")
                return render_template(
                    'volunteer_login.html',
                    error=translate_text(lang, "Invalid password. Please try again.", "చెల్లని సంకేతపదం. దయచేసి మళ్లీ ప్రయత్నించండి."),
                    lang=lang
                )
           
            session['volunteer_id'] = str(volunteer['_id'])
            logger.info(f"Volunteer {name} logged in successfully, redirecting to dashboard")
            flash(translate_text(lang, "Volunteer login successful! Welcome to the dashboard.", "సేవకుల లాగిన్ విజయవంతం! డ్యాష్‌బోర్డ్‌కు స్వాగతం."), "success")
            return redirect('/volunteer')
        except Exception as e:
            logger.error(f"Error in volunteer login: {e}")
            return render_template(
                'volunteer_login.html',
                error=translate_text(lang, f"Login error: {str(e)}", f"లాగిన్ లోపం: {str(e)}"),
                lang=lang
            )
   
    logger.info("Rendering volunteer login page")
    return render_template('volunteer_login.html', lang=lang)

@app.route('/volunteer_reg', methods=['GET', 'POST'])
def volunteer_reg():
    lang = session.get('preferred_lang', 'en').lower()
    if request.method == 'POST':
        name = request.form.get('name', '').strip()
        password = request.form.get('password', '').strip()
        phone = request.form.get('phone', '').strip()
        village = request.form.get('village', '').strip().lower()
        if not all([name, password, phone, village]):
            return render_template(
                'volunteer_reg.html',
                error=translate_text(lang, "All fields required", "అన్ని ఫీల్డ్లు అవసరం"),
                villages=villages,
                lang=lang
            )
        if volunteers_collection.find_one({"name": name}):
            return render_template(
                'volunteer_reg.html',
                error=translate_text(lang, "Name already exists", "పేరు ఇప్పటికే ఉంది"),
                villages=villages,
                lang=lang
            )
        if village not in [v.lower() for v in villages]:
            return render_template(
                'volunteer_reg.html',
                error=translate_text(lang, "Invalid village", "చెల్లని గ్రామం"),
                villages=villages,
                lang=lang
            )
        try:
            volunteer_data = {
                "name": name,
                "password": generate_password_hash(password),
                "phone": phone,
                "village": village
            }
            volunteers_collection.insert_one(volunteer_data)
            logger.info(f"Volunteer {name} registered successfully, redirecting to login")
            flash(translate_text(
                lang,
                "Volunteer registration completed successfully! Please login.",
                "సేవకుల నమోదు విజయవంతమైంది! దయచేసి లాగిన్ చేయండి."
            ), "success")
            return redirect('/volunteer_login')
        except Exception as e:
            logger.error(f"Error registering volunteer {name}: {e}")
            return render_template(
                'volunteer_reg.html',
                error=translate_text(lang, f"Registration failed: {str(e)}", f"నమోదు విఫలమైంది: {str(e)}"),
                villages=villages,
                lang=lang
            )
   
    logger.info("Rendering volunteer registration page")
    return render_template('volunteer_reg.html', villages=villages, lang=lang)

@app.route('/volunteer', methods=['GET', 'POST'])
def volunteer():
    if 'volunteer_id' not in session:
        logger.warning("Unauthorized access to volunteer dashboard - redirecting to login")
        return redirect('/volunteer_login')
   
    volunteer = volunteers_collection.find_one({"_id": ObjectId(session['volunteer_id'])})
    if not volunteer:
        logger.warning("Volunteer session invalid - redirecting to login")
        session.pop('volunteer_id', None)
        return redirect('/volunteer_login')
   
    lang = session.get('preferred_lang', 'en').lower()
    if lang not in VOLUNTEER_STATUS_LABELS:
        lang = 'en'
    translator = make_translator(lang)
    flash_config = FARMER_UI_LABELS.get(lang, FARMER_UI_LABELS['en'])
    flash_labels = flash_config['flash_levels']
    flash_default = flash_config['flash_default']

    message = None
    message_type = 'info'

    if request.method == 'POST':
        action = (request.form.get('action') or '').strip()

        if action == 'decline_request':
            help_request_id = (request.form.get('request_id') or '').strip()
            if help_request_id:
                try:
                    result = requests_collection.update_one(
                        {"_id": ObjectId(help_request_id), "volunteer_id": session['volunteer_id']},
                        {"$set": {"status": "declined", "responded_at": time.time()}}
                    )
                    if result.modified_count:
                        message = translate_text(lang, "Request declined.", "అభ్యర్థనను తిరస్కరించారు.")
                        message_type = 'info'
                    else:
                        message = translate_text(lang, "Request not found or already processed.", "అభ్యర్థన కనబడలేదు లేదా ఇప్పటికే ప్రాసెస్ చేయబడింది.")
                        message_type = 'warning'
                except Exception as e:
                    logger.error(f"Error declining request {help_request_id}: {e}")
                    message = translate_text(lang, f"Error processing request: {str(e)}", f"అభ్యర్థనను ప్రాసెస్ చేయడంలో లోపం: {str(e)}")
                    message_type = 'error'
            else:
                message = translate_text(lang, "Invalid request ID", "చెల్లని అభ్యర్థన ID")
                message_type = 'error'

        elif action == 'accept_request':
            help_request_id = (request.form.get('request_id') or '').strip()
            help_request = None
            if help_request_id:
                try:
                    help_request = requests_collection.find_one({
                        "_id": ObjectId(help_request_id),
                        "volunteer_id": session['volunteer_id']
                    })
                except Exception as e:
                    logger.error(f"Error fetching help request {help_request_id}: {e}")
                    help_request = None

            if not help_request:
                message = translate_text(lang, "Request not found or already processed.", "అభ్యర్థన కనబడలేదు లేదా ఇప్పటికే ప్రాసెస్ చేయబడింది.")
                message_type = 'error'
            else:
                request_type = help_request.get('request_type') or 'farmer_help'

                if request_type == 'farmer_help':
                    farmer = None
                    farmer_id = help_request.get('farmer_id')
                    if farmer_id:
                        try:
                            farmer = farmers_collection.find_one({"_id": ObjectId(farmer_id)})
                        except Exception:
                            farmer = None

                    if not farmer:
                        message = translate_text(lang, "Farmer details not available for this request.", "ఈ అభ్యర్థనకు రైతు వివరాలు అందుబాటులో లేవు.")
                        message_type = 'error'
                    else:
                        task_name_input = (request.form.get('task_name') or 'General Farm Help').strip()
                        task_village_input = (request.form.get('task_village') or farmer.get('village', 'gudlavalleru')).strip().lower()
                        wage_input = (request.form.get('task_wage') or '').strip()
                        workers_input = (request.form.get('task_workers') or '').strip()
                        description_input = (request.form.get('task_description') or '').strip()

                        errors = []
                        try:
                            wage_value = float(wage_input) if wage_input else 0.0
                            if wage_value < 0:
                                errors.append(translate_text(lang, "Wage cannot be negative.", "వేతనం ప్రతికూలంగా ఉండకూడదు."))
                        except ValueError:
                            errors.append(translate_text(lang, "Enter a valid wage amount.", "సరైన వేతన మొత్తాన్ని నమోదు చేయండి."))
                            wage_value = 0.0

                        try:
                            workers_value = int(workers_input) if workers_input else 2
                            if workers_value < 1 or workers_value > 50:
                                errors.append(translate_text(lang, "Number of workers must be between 1 and 50.", "కార్మికుల సంఖ్య 1 నుండి 50 మధ్య ఉండాలి."))
                        except ValueError:
                            errors.append(translate_text(lang, "Enter a valid number of workers.", "సరైన కార్మికుల సంఖ్యను నమోదు చేయండి."))
                            workers_value = 2

                        if task_village_input not in [v.lower() for v in villages]:
                            errors.append(translate_text(lang, "Please choose a valid village for the task.", "దయచేసి పనికి చెల్లుబాటు అయ్యే గ్రామాన్ని ఎంచుకోండి."))

                        if errors:
                            message = errors[0]
                            message_type = 'error'
                        else:
                            location = f"{task_village_input}, gudlavalleru"
                            coordinates = get_coordinates(location, task_village_input)
                            if not coordinates:
                                message = translate_text(lang, "Could not find coordinates for the selected village.", "ఎంచుకున్న గ్రామానికి స్థానాంశాలు లభించలేదు.")
                                message_type = 'error'
                            else:
                                task_description = description_input or f"Assist farmer {farmer.get('name', 'Unknown')} with {task_name_input.lower()}."
                                task_data = {
                                        "location": location,
                                        "task": task_name_input,
                                        "village": task_village_input.capitalize(),
                                        "mandal": "Gudlavalleru",
                                        "coordinates": {"type": "Point", "coordinates": coordinates},
                                        "wage": wage_value,
                                        "num_workers": workers_value,
                                        "description": task_description,
                                        "posted_by": session['volunteer_id'],
                                        "farmer_id": help_request.get('farmer_id'),
                                        "help_request_id": str(help_request_id),
                                        "accepted_by": None,
                                        "accepted_workers": [],
                                        "worker_assignments": [],
                                        "denied_by": [],
                                        "created_at": time.time(),
                                        "status": "open",
                                        "completed": False,
                                        "completed_at": None,
                                        "task_type": "help_request"
                                    }
                                try:
                                    result = tasks_collection.insert_one(task_data)
                                    if result.inserted_id:
                                        requests_collection.update_one(
                                            {"_id": ObjectId(help_request_id)},
                                            {"$set": {
                                                "status": "accepted",
                                                "responded_at": time.time(),
                                                "responded_by": session['volunteer_id'],
                                                "assigned_task_id": str(result.inserted_id),
                                                "task_summary": task_name_input
                                            }}
                                        )
                                        task_snapshot = task_data.copy()
                                        task_snapshot['_id'] = result.inserted_id
                                        farmer_reference = task_data.get('farmer_id')
                                        farmer_doc = None
                                        if farmer_reference:
                                            try:
                                                farmer_doc = farmers_collection.find_one({"_id": ObjectId(farmer_reference)})
                                            except Exception as farmer_lookup_error:
                                                logger.error(f"Unable to fetch farmer {farmer_reference} for SMS: {farmer_lookup_error}")
                                        notify_nearby_workers_of_task(task_snapshot, farmer_doc)
                                        message = translate_text(
                                            lang,
                                            f"Task '{task_name_input}' posted for {task_village_input.capitalize()}. Workers will now see it.",
                                            f"'{task_name_input}' పనిని {task_village_input.capitalize()} కోసం పోస్ట్ చేశారు. కార్మికులు ఇప్పుడు దీన్ని చూడగలరు."
                                        )
                                        message_type = 'success'
                                        logger.info(f"Volunteer {volunteer['name']} created task {task_name_input} for request {help_request_id}")
                                    else:
                                        message = translate_text(
                                            lang,
                                            "Request accepted but failed to create worker task. Please try again.",
                                            "అభ్యర్థన అంగీకరించబడింది కానీ కార్మికుల పనిని సృష్టించలేకపోయాం. దయచేసి మళ్లీ ప్రయత్నించండి."
                                        )
                                        message_type = 'warning'
                                except DuplicateKeyError:
                                    message = translate_text(
                                        lang,
                                        "A similar task is already open for this farmer and location.",
                                        "ఈ రైతు మరియు ప్రదేశం కోసం ఇప్పటికే ఇలాంటి పని ఓపెన్‌లో ఉంది."
                                    )
                                    message_type = 'warning'
                                    logger.info(
                                        "Duplicate helper task prevented for volunteer %s (request %s, location %s, task %s)",
                                        session.get('volunteer_id'),
                                        help_request_id,
                                        location,
                                        task_name_input
                                    )
                                except Exception as e:
                                    logger.error(f"Error creating task for help request {help_request_id}: {e}")
                                    message = translate_text(lang, f"Error creating task: {str(e)}", f"పని సృష్టించడంలో లోపం: {str(e)}")
                                    message_type = 'error'

                elif request_type == 'worker_help':
                    worker_name = help_request.get('worker_name', 'Worker')
                    worker_phone = (help_request.get('worker_phone') or '').strip()
                    worker_village = help_request.get('worker_village')
                    task_details = help_request.get('task_details') or {}
                    task_name = task_details.get('task_name') or 'Not specified'
                    task_location = task_details.get('task_location') or 'Not specified'
                    task_wage = task_details.get('task_wage') or '—'

                    message_en = f"Request accepted! Please contact {worker_name}"
                    if worker_phone:
                        message_en += f" at {worker_phone}"
                    if worker_village:
                        message_en += f" from {str(worker_village).capitalize()}"
                    notes = help_request.get('worker_notes')
                    message_en += "."
                    if notes:
                        message_en += f" Note: {notes}."
                    message_en += f" Task: {task_name} at {task_location} (₹{task_wage}/day)."

                    message_te = f"అభ్యర్థన అంగీకరించబడింది! దయచేసి {worker_name}"
                    if worker_phone:
                        message_te += f" ను {worker_phone} నంబర్‌లో సంప్రదించండి"
                    else:
                        message_te += "ను సంప్రదించండి"
                    if worker_village:
                        message_te += f" ({str(worker_village).capitalize()}) నుంచి"
                    message_te += "."
                    if notes:
                        message_te += f" గమనిక: {notes}."
                    message_te += f" పని: {task_name} — {task_location} (₹{task_wage}/రోజు)."

                    message = translate_text(lang, message_en, message_te)

                    try:
                        create_worker_message(
                            help_request.get('worker_id'),
                            worker_phone,
                            session.get('volunteer_id'),
                            volunteer.get('phone') if volunteer else None,
                            task_details,
                            worker_name,
                            volunteer.get('name') if volunteer else None
                        )
                    except Exception as e:
                        logger.error(f"Error sending worker notification for request {help_request_id}: {e}")

                    task_id_for_update = normalize_object_id_str(task_details.get('task_id'))
                    worker_id_for_update = normalize_object_id_str(help_request.get('worker_id'))
                    if task_id_for_update:
                        if worker_id_for_update:
                            marked = mark_task_accepted_by_worker(
                                task_id_for_update,
                                worker_id_for_update,
                                worker_name=worker_name,
                                worker_phone=worker_phone
                            )
                            if not marked:
                                logger.warning("Failed to mark task %s as accepted for worker help request %s.", task_id_for_update, help_request_id)
                        else:
                            logger.warning("Worker id missing for help request %s; task %s not updated.", help_request_id, task_id_for_update)

                    requests_collection.update_one(
                        {"_id": ObjectId(help_request_id)},
                        {"$set": {"status": "accepted", "responded_at": time.time(), "responded_by": session['volunteer_id']}}
                    )
                    message_type = 'success'
                else:
                    requests_collection.update_one(
                        {"_id": ObjectId(help_request_id)},
                        {"$set": {"status": "accepted", "responded_at": time.time(), "responded_by": session['volunteer_id']}}
                    )
                    message = translate_text(lang, "Request accepted.", "అభ్యర్థన అంగీకరించబడింది.")
                    message_type = 'success'

        elif action == 'create_task':
            village = (request.form.get('village') or '').strip().lower()
            task = (request.form.get('task') or '').strip()
            wage = (request.form.get('wage') or '').strip()
            num_workers = (request.form.get('num_workers') or '').strip()
            description = (request.form.get('description') or '').strip()

            logger.info(f"Volunteer {volunteer['name']} attempting to post task: {task} in {village}, gudlavalleru")

            if not all([village, task]):
                message = translate_text(lang, "All required fields must be filled", "అన్ని తప్పనిసరి ఫీల్డ్స్‌ను పూరించండి")
                message_type = 'error'
            elif village not in [v.lower() for v in villages]:
                message = translate_text(lang, "Please select a valid village", "దయచేసి చెల్లుబాటు అయ్యే గ్రామాన్ని ఎంచుకోండి")
                message_type = 'error'
            elif task not in task_labels:
                message = translate_text(lang, "Please select a valid task type", "దయచేసి చెల్లుబాటు అయ్యే పనిని ఎంచుకోండి")
                message_type = 'error'
            else:
                try:
                    wage_value = float(wage) if wage else 0.0
                    num_workers_value = int(num_workers) if num_workers else 1

                    if wage_value < 0:
                        message = translate_text(lang, "Wage cannot be negative", "వేతనం ప్రతికూలంగా ఉండకూడదు")
                        message_type = 'error'
                    elif num_workers_value < 1 or num_workers_value > 50:
                        message = translate_text(lang, "Number of workers must be between 1 and 50", "కార్మికుల సంఖ్య 1 నుండి 50 మధ్య ఉండాలి")
                        message_type = 'error'
                    else:
                        location = f"{village}, gudlavalleru"
                        coordinates = get_coordinates(location, village)

                        if not coordinates:
                            message = translate_text(lang, "Could not find coordinates for the specified village.", "ఎంచుకున్న గ్రామానికి స్థానాంశాలు లభించలేదు.")
                            message_type = 'error'
                        else:
                            task_data = {
                                "location": location,
                                "task": task,
                                "village": village.capitalize(),
                                "mandal": "Gudlavalleru",
                                "coordinates": {"type": "Point", "coordinates": coordinates},
                                "wage": wage_value,
                                "num_workers": num_workers_value,
                                "description": description,
                                "posted_by": session['volunteer_id'],
                                "accepted_by": None,
                                "accepted_workers": [],
                                "worker_assignments": [],
                                "denied_by": [],
                                "created_at": time.time(),
                                "status": "open",
                                "completed": False,
                                "completed_at": None
                            }

                            existing_task = tasks_collection.find_one({
                                "location": location,
                                "task": task,
                                "posted_by": session['volunteer_id'],
                                "status": "open"
                            })

                            if existing_task:
                                message = translate_text(
                                    lang,
                                    f"A similar {task} task already exists for {location.capitalize()}",
                                    f"{location.capitalize()} కోసం '{task}' పని ఇప్పటికే ఉంది"
                                )
                                message_type = 'warning'
                            else:
                                try:
                                    result = tasks_collection.insert_one(task_data)
                                except DuplicateKeyError:
                                    message = translate_text(
                                        lang,
                                        f"A similar {task} task is already open for {location.capitalize()}.",
                                        f"{location.capitalize()} కోసం '{task}' పని ఇప్పటికే ఓపెన్‌లో ఉంది."
                                    )
                                    message_type = 'warning'
                                    logger.info(
                                        "Duplicate volunteer task prevented for volunteer %s at %s (%s)",
                                        session.get('volunteer_id'),
                                        location,
                                        task
                                    )
                                except Exception as insert_error:
                                    message = translate_text(lang, f"An error occurred: {str(insert_error)}", f"లోపం జరిగింది: {str(insert_error)}")
                                    message_type = 'error'
                                    logger.error(f"Error in volunteer task posting: {insert_error}")
                                else:
                                    if result.inserted_id:
                                        message = translate_text(
                                            lang,
                                            f"✅ Task '{task}' successfully posted for {location.capitalize()} with wage ₹{wage_value}/day for {num_workers_value} worker(s)",
                                            f"✅ '{task}' పనిని {location.capitalize()} కోసం విజయవంతంగా పోస్ట్ చేశారు – ₹{wage_value}/రోజు, {num_workers_value} కార్మికుల కోసం"
                                        )
                                        message_type = 'success'
                                        logger.info(f"Task {task} posted successfully for {location} by volunteer {session['volunteer_id']}")
                                    else:
                                        message = translate_text(lang, "Failed to post task. Please try again.", "పనిని పోస్ట్ చేయడం విఫలమైంది. దయచేసి మళ్లీ ప్రయత్నించండి.")
                                        message_type = 'error'
                except ValueError as e:
                    message = translate_text(lang, "Invalid wage or number of workers format", "వేతనం లేదా కార్మికుల సంఖ్య ఆకృతి సరైనది కాదు")
                    message_type = 'error'
                    logger.error(f"Value error in task posting: {e}")
                except Exception as e:
                    message = translate_text(lang, f"An error occurred: {str(e)}", f"లోపం జరిగింది: {str(e)}")
                    message_type = 'error'
                    logger.error(f"Error in volunteer task posting: {e}")
   
    # Fetch pending requests for this volunteer with farmer names and details
    pending_requests_raw = list(requests_collection.find({"volunteer_id": session['volunteer_id'], "status": "pending"}))
    pending_requests = []
    for help_request in pending_requests_raw:
        help_request['id'] = str(help_request.get('_id'))
        request_type = help_request.get('request_type') or 'farmer_help'
        help_request['request_type'] = request_type

        if request_type == 'farmer_help':
            farmer = None
            farmer_id = help_request.get('farmer_id')
            if farmer_id:
                try:
                    farmer = farmers_collection.find_one({"_id": ObjectId(farmer_id)})
                except Exception:
                    farmer = None
            if farmer:
                help_request['farmer_name'] = farmer.get('name', 'Unknown Farmer')
                help_request['farmer_details'] = {
                    'village': farmer.get('village', ''),
                    'phone': farmer.get('phone', ''),
                    'location': farmer.get('location', '')
                }
            else:
                help_request['farmer_name'] = 'Unknown Farmer'
                help_request['farmer_details'] = None
        elif request_type == 'worker_help':
            worker_profile = None
            worker_id_ref = help_request.get('worker_id')
            if worker_id_ref:
                try:
                    worker_profile = workers_collection.find_one({"_id": ObjectId(worker_id_ref)})
                except Exception:
                    worker_profile = None
            rating_count = worker_profile.get('rating_count', 0) if worker_profile else 0
            rating_total = worker_profile.get('rating_total', 0) if worker_profile else 0
            average_rating = round(rating_total / rating_count, 1) if rating_count else None
            help_request['worker_details'] = {
                'id': worker_id_ref,
                'name': help_request.get('worker_name', 'Worker'),
                'phone': help_request.get('worker_phone', ''),
                'village': help_request.get('worker_village', ''),
                'notes': help_request.get('worker_notes', ''),
                'preferred_wage': worker_profile.get('preferred_wage') if worker_profile else None,
                'availability': worker_profile.get('availability') if worker_profile else None,
                'skills': worker_profile.get('skills', []) if worker_profile else [],
                'average_rating': average_rating,
                'rating_count': rating_count
            }
        pending_requests.append(help_request)

    volunteer_tasks = list(tasks_collection.find({"posted_by": session['volunteer_id']}).sort("created_at", -1).limit(8))
    for vt in volunteer_tasks:
        vt['id'] = str(vt.get('_id'))

    logger.info(f"Rendering volunteer dashboard for {volunteer['name']} with {len(pending_requests)} pending requests")
    return render_template(
        'volunteer.html',
        message=message,
        message_type=message_type,
        tasks=task_labels,
        villages=villages,
        volunteer=volunteer,
        pending_requests=pending_requests,
        volunteer_tasks=volunteer_tasks,
        volunteer_status_labels=VOLUNTEER_STATUS_LABELS.get(lang, VOLUNTEER_STATUS_LABELS['en']),
        lang=lang,
        translate=translator,
        flash_labels=flash_labels,
        flash_default=flash_default
    )

@app.route('/worker_login', methods=['GET', 'POST'])
def worker_login():
    lang = session.get('preferred_lang', 'en').lower()
    if request.method == 'POST':
        try:
            name = request.form.get('name', '').strip()
            password = request.form.get('password', '').strip()
           
            logger.info(f"Worker login attempt for: {name}")
           
            if not name or not password:
                return render_template(
                    'worker_login.html',
                    error=translate_text(lang, "Please enter both name and password", "దయచేసి పేరు మరియు సంకేతపదాన్ని నమోదు చేయండి."),
                    lang=lang
                )
           
            worker = workers_collection.find_one({"name": name})
           
            if not worker:
                logger.warning(f"Worker not found: {name}")
                return render_template(
                    'worker_login.html',
                    error=translate_text(lang, "Worker not found. Please check your name or register first.", "కార్మికుడు కనబడలేదు. మీ పేరును తనిఖీ చేయండి లేదా ముందుగా నమోదు చేసుకోండి."),
                    lang=lang
                )
           
            if not check_password_hash(worker['password'], password):
                logger.warning(f"Invalid password for worker: {name}")
                return render_template(
                    'worker_login.html',
                    error=translate_text(lang, "Invalid password. Please try again.", "చెల్లని సంకేతపదం. దయచేసి మళ్లీ ప్రయత్నించండి."),
                    lang=lang
                )
           
            session['worker_id'] = str(worker['_id'])
            logger.info(f"Worker {name} logged in successfully, redirecting to dashboard")
            flash(translate_text(lang, "Worker login successful! Welcome to the dashboard.", "కార్మికుల లాగిన్ విజయవంతం! డ్యాష్‌బోర్డ్‌కు స్వాగతం."), "success")
            return redirect('/worker')
        except Exception as e:
            logger.error(f"Error in worker login: {e}")
            return render_template(
                'worker_login.html',
                error=translate_text(lang, f"Login error: {str(e)}", f"లాగిన్ లోపం: {str(e)}"),
                lang=lang
            )
   
    logger.info("Rendering worker login page")
    return render_template('worker_login.html', lang=lang)

@app.route('/worker_reg', methods=['GET', 'POST'])
def worker_reg():
    lang = session.get('preferred_lang', 'en').lower()
    if request.method == 'POST':
        name = request.form.get('name', '').strip()
        password = request.form.get('password', '').strip()
        phone = request.form.get('phone', '').strip()
        village = request.form.get('village', '').strip().lower()
        preferred_wage_raw = request.form.get('preferred_wage', '').strip()
        availability = (request.form.get('availability') or '').strip().lower()
        skills_raw = request.form.get('skills', '').strip()
        location = f"{village}, gudlavalleru"
        if not all([name, password, phone, village]):
            return render_template(
                'worker_reg.html',
                error=translate_text(lang, "All fields required", "అన్ని ఫీల్డ్లు అవసరం"),
                villages=villages,
                lang=lang
            )
        if workers_collection.find_one({"name": name}):
            return render_template(
                'worker_reg.html',
                error=translate_text(lang, "Name already exists", "పేరు ఇప్పటికే ఉంది"),
                villages=villages,
                lang=lang
            )
        if village not in [v.lower() for v in villages]:
            return render_template(
                'worker_reg.html',
                error=translate_text(lang, "Invalid village", "చెల్లని గ్రామం"),
                villages=villages,
                lang=lang
            )

        preferred_wage_value = None
        if preferred_wage_raw:
            try:
                preferred_wage_value = float(preferred_wage_raw)
                if preferred_wage_value < 0:
                    raise ValueError("Preferred wage negative")
            except ValueError:
                return render_template(
                    'worker_reg.html',
                    error=translate_text(lang, "Enter a valid preferred wage", "చెల్లుబాటు అయ్యే వేతనాన్ని నమోదు చేయండి"),
                    villages=villages,
                    lang=lang
                )

        availability_options = {"full_time", "half_day", "weekends", "seasonal", ""}
        if availability not in availability_options:
            return render_template(
                'worker_reg.html',
                error=translate_text(lang, "Select a valid availability option", "చెల్లుబాటు అయ్యే లభ్యతను ఎంచుకోండి"),
                villages=villages,
                lang=lang
            )

        skills_list = [skill.strip() for skill in skills_raw.split(',') if skill.strip()] if skills_raw else []
        coordinates = get_coordinates(location, village)
        if not coordinates:
            return render_template(
                'worker_reg.html',
                error=translate_text(lang, "Could not find location", "స్థానాన్ని కనుగొనలేకపోయాం"),
                villages=villages,
                lang=lang
            )
        try:
            worker_data = {
                "name": name,
                "password": generate_password_hash(password),
                "phone": phone,
                "location": location.capitalize(),
                "coordinates": {"type": "Point", "coordinates": coordinates},
                "mandal": "gudlavalleru",
                "village": village,
                "accepted_tasks": [],
                "denied_tasks": [],
                "preferred_wage": preferred_wage_value,
                "availability": availability or None,
                "skills": skills_list
            }
            workers_collection.insert_one(worker_data)
            logger.info(f"Worker {name} registered successfully, redirecting to login")
            flash(translate_text(
                lang,
                "Worker registration completed successfully! Please login.",
                "కార్మికుల నమోదు విజయవంతమైంది! దయచేసి లాగిన్ చేయండి."
            ), "success")
            return redirect('/worker_login')
        except Exception as e:
            logger.error(f"Error registering worker {name}: {e}")
            return render_template(
                'worker_reg.html',
                error=translate_text(lang, f"Registration failed: {str(e)}", f"నమోదు విఫలమైంది: {str(e)}"),
                villages=villages,
                lang=lang
            )
   
    logger.info("Rendering worker registration page")
    return render_template('worker_reg.html', villages=villages, lang=lang)

@app.route('/worker', methods=['GET', 'POST'])
def worker():
    if 'worker_id' not in session:
        logger.warning("Unauthorized access to worker dashboard - redirecting to login")
        return redirect('/worker_login')
   
    worker = workers_collection.find_one({"_id": ObjectId(session['worker_id'])})
    if not worker:
        logger.warning("Worker session invalid - redirecting to login")
        session.pop('worker_id', None)
        return redirect('/worker_login')

    rating_count = worker.get('rating_count', 0)
    rating_total = worker.get('rating_total', 0)
    try:
        average_rating = round(rating_total / rating_count, 1) if rating_count else None
    except Exception:
        average_rating = None
    worker['rating_count'] = rating_count
    worker['rating_average'] = average_rating

    lang = session.get('preferred_lang', 'en').lower()
    if lang not in ('en', 'te'):
        lang = 'en'
    translator = make_translator(lang)
    flash_config = FARMER_UI_LABELS.get(lang, FARMER_UI_LABELS['en'])
    flash_labels = flash_config['flash_levels']
    flash_default = flash_config['flash_default']

    if request.method == 'POST':
        action = request.form.get('action', '').strip()
        if action == 'delete_notification':
            notification_id = request.form.get('notification_id', '').strip()
            if notification_id:
                try:
                    delete_criteria = {"_id": ObjectId(notification_id), "role": "worker"}
                    ownership_filters = [{"worker_id": session['worker_id']}]
                    worker_phone = worker.get('phone')
                    if worker_phone:
                        ownership_filters.append({"worker_phone": worker_phone})
                    delete_criteria["$or"] = ownership_filters

                    delete_result = notifications_collection.delete_one(delete_criteria)
                    if delete_result.deleted_count:
                        flash(translate_text(lang, "Notification removed.", "అధిసూచన తొలగించబడింది."), "success")
                    else:
                        flash(translate_text(lang, "Notification not found or already removed.", "అధిసూచన కనబడలేదు లేదా ఇప్పటికే తొలగించబడింది."), "warning")
                except Exception as err:
                    flash(translate_text(lang, "Could not delete notification.", "అధిసూచనను తొలగించలేకపోయాం."), "danger")
                    logger.error(f"Error deleting worker notification {notification_id}: {err}")
            else:
                flash(translate_text(lang, "Invalid notification selected.", "చెల్లని అధిసూచనను ఎంచుకున్నారు."), "warning")
            return redirect('/worker')
        elif action == 'clear_notifications':
            worker_id = session.get('worker_id')
            if worker_id:
                try:
                    notifications_collection.delete_many({"worker_id": worker_id})
                    notifications_collection.delete_many({"worker_phone": worker.get('phone')})
                    flash(translate_text(lang, "Messages cleared.", "సందేశాలు తొలగించబడ్డాయి."), 'success')
                except Exception as err:
                    logger.error(f"Error clearing worker notifications for {worker_id}: {err}")
                    flash(translate_text(lang, "Could not clear messages.", "సందేశాలను తొలగించలేకపోయాం."), 'warning')
            return redirect('/worker')
        elif action == 'request_volunteer':
            task_name = (request.form.get('task_name') or '').strip()
            task_wage = (request.form.get('task_wage') or '').strip()
            task_location = (request.form.get('task_location') or '').strip()
            task_id = (request.form.get('task_id') or '').strip()
            task_village = (request.form.get('task_village') or '').strip().lower()
            worker_name = worker.get('name', 'Worker')
            worker_phone = (worker.get('phone') or '').strip()
            worker_village = task_village or (worker.get('village') or '').strip().lower()

            if not worker_phone:
                flash(translate_text(lang, "Please update your phone number before requesting volunteer help.", "సేవకుల సహాయం కోరే ముందు దయచేసి మీ ఫోన్ నంబర్‌ను నవీకరించండి."), "warning")
                return redirect('/worker')

            volunteers_target = []
            try:
                if worker_village:
                    volunteers_target = list(volunteers_collection.find({"village": worker_village}).limit(3))
            except Exception as err:
                logger.error(f"Error fetching volunteers for village {worker_village}: {err}")
                volunteers_target = []

            if len(volunteers_target) < 3:
                try:
                    exclude_ids = [vol.get('_id') for vol in volunteers_target if vol.get('_id')]
                    additional_limit = 3 - len(volunteers_target)
                    if additional_limit > 0:
                        additional = list(volunteers_collection.find({"_id": {"$nin": exclude_ids}}).limit(additional_limit))
                        volunteers_target.extend(additional)
                except Exception as err:
                    logger.error(f"Error fetching fallback volunteers: {err}")

            selected_volunteers = []
            seen_ids = set()
            for vol in volunteers_target:
                vid = vol.get('_id') if vol else None
                if vid and vid not in seen_ids:
                    selected_volunteers.append(vol)
                    seen_ids.add(vid)
                if len(selected_volunteers) >= 3:
                    break

            if not selected_volunteers:
                flash(translate_text(lang, "No volunteers are available right now. Please try again later.", "ప్రస్తుతం సేవకులు అందుబాటులో లేరు. కొద్దిసేపటి తరువాత మళ్లీ ప్రయత్నించండి."), "warning")
                return redirect('/worker')

            request_payload = {
                "request_type": "worker_help",
                "worker_id": session.get('worker_id'),
                "worker_name": worker_name,
                "worker_phone": worker_phone,
                "worker_village": worker_village or None,
                "status": "pending",
                "created_at": time.time()
            }

            task_details = {}
            if any([task_name, task_wage, task_location, task_id]):
                task_details = {
                    "task_name": task_name,
                    "task_wage": task_wage,
                    "task_location": task_location,
                    "task_id": task_id
                }

            created_requests = 0
            for volunteer in selected_volunteers:
                volunteer_id = volunteer.get('_id')
                if not volunteer_id:
                    continue
                volunteer_id_str = str(volunteer_id)

                try:
                    existing_request = requests_collection.find_one({
                        "request_type": "worker_help",
                        "status": "pending",
                        "volunteer_id": volunteer_id_str,
                        "$or": [
                            {"worker_id": session.get('worker_id')},
                            {"worker_phone": worker_phone}
                        ]
                    })
                except Exception as err:
                    logger.error(f"Error checking existing worker help request: {err}")
                    existing_request = None

                if existing_request:
                    continue

                request_document = request_payload.copy()
                request_document["volunteer_id"] = volunteer_id_str
                if task_details:
                    request_document["task_details"] = task_details

                try:
                    requests_collection.insert_one(request_document)
                    created_requests += 1
                except Exception as err:
                    logger.error(f"Error creating worker help request for volunteer {volunteer_id_str}: {err}")

            if created_requests:
                session['worker_help_modal'] = {
                    "title": translate_text(lang, "Request sent", "అభ్యర్థన పంపబడింది"),
                    "body": translate_text(
                        lang,
                        f"Sent your help request to {created_requests} volunteer(s). They'll reach out soon.",
                        f"{created_requests} సేవకులకు మీ సహాయ అభ్యర్థన పంపించబడింది. వారు త్వరలో సంప్రదిస్తారు."
                    ),
                    "count": created_requests
                }
            else:
                flash(translate_text(lang, "You already have a pending help request with available volunteers.", "మీ వద్ద ఇప్పటికే సేవకుల సహాయ అభ్యర్థన పెండింగ్‌లో ఉంది."), "info")
            return redirect('/worker')
        else:
            if action:
                logger.debug("Worker POST action processed without redirect: %s", action)
   
    nearby_tasks = []
    message = None
    message_type = 'info'
    latest_messages = []
    action = request.form.get('action') if request.method == 'POST' else None

    def load_nearby_tasks():
        local_tasks = []
        local_message = None
        local_type = 'info'
        try:
            worker_coords = worker['coordinates']['coordinates']
            worker_lon, worker_lat = worker_coords

            logger.info(f"Worker {worker['name']} at {worker['location']} fetching tasks (coords: {worker_coords})")

            all_tasks = list(tasks_collection.find({
                "status": {"$in": ["open", "in_progress", "filled"]},
                "denied_by": {"$nin": [session['worker_id']]}
            }).sort("created_at", -1))

            logger.info(f"Found {len(all_tasks)} open tasks in database")

            for task in all_tasks:
                if 'coordinates' in task and 'coordinates' in task['coordinates']:
                    task_coords = task['coordinates']['coordinates']
                    task_lon, task_lat = task_coords

                    accepted_workers = task.get('accepted_workers') or []
                    try:
                        required = int(task.get('num_workers', 1))
                    except Exception:
                        required = 1
                    if session['worker_id'] in accepted_workers:
                        continue
                    if len(accepted_workers) >= required:
                        continue

                    assigned_to = task.get('assigned_to')
                    if assigned_to and assigned_to != session['worker_id']:
                        continue

                    distance = haversine_distance(worker_lat, worker_lon, task_lat, task_lon)
                    if distance is not None and distance <= 30.0:
                        task['distance_km'] = round(distance, 2)
                        local_tasks.append(task)
                        logger.info(f"Task {task['task']} at {task['location']} added to nearby tasks (distance: {distance:.2f} km, coords: {task_coords})")

            local_tasks.sort(key=lambda x: x.get('distance_km', 999))

            unique_tasks = {}
            for task in local_tasks:
                task_id = str(task['_id'])
                if task_id not in unique_tasks:
                    unique_tasks[task_id] = task
            local_tasks = list(unique_tasks.values())

            if local_tasks:
                local_message = translate_text(
                    lang,
                    f"Found {len(local_tasks)} nearby tasks within 30km radius",
                    f"30 కి.మీ పరిధిలో {len(local_tasks)} సమీప పనులు లభించాయి."
                )
                local_type = 'success'
            else:
                local_message = translate_text(
                    lang,
                    "No tasks found within 30km radius. Try checking later.",
                    "30 కి.మీ పరిధిలో పనులు లభించలేదు. కొంతసేపటి తరువాత మళ్లీ చూడండి."
                )
                local_type = 'info'
        except Exception as fetch_error:
            logger.error(f"Error fetching tasks: {fetch_error}")
            local_message = translate_text(
                lang,
                f"Error fetching tasks: {str(fetch_error)}",
                f"పనులను పొందడంలో లోపం: {str(fetch_error)}"
            )
            local_type = 'error'

        return local_tasks, local_message, local_type

    # Handle task actions: accept, deny, complete
    if request.method == 'POST':
        task_id = request.form.get('task_id')
        if action in ['accept', 'deny', 'complete'] and task_id:
            try:
                task = tasks_collection.find_one({"_id": ObjectId(task_id)})
                if not task:
                    message = translate_text(lang, "Task not found", "పని కనబడలేదు")
                    message_type = 'error'
                elif action == 'accept':
                    accepted_workers = task.get('accepted_workers') or []
                    try:
                        required = int(task.get('num_workers', 1))
                    except Exception:
                        required = 1
                    if session['worker_id'] in accepted_workers:
                        message = translate_text(lang, "You already accepted this task", "మీరు ఈ పనిని ఇప్పటికే అంగీకరించారు")
                        message_type = 'info'
                    elif len(accepted_workers) >= required:
                        message = translate_text(lang, "This task is no longer available", "ఈ పని ఇక అందుబాటులో లేదు")
                        message_type = 'warning'
                    else:
                        worker_id_str = session['worker_id']
                        accepted_workers.append(worker_id_str)
                        assignments = task.get('worker_assignments') or []
                        already_tracked = any(a.get('worker_id') == worker_id_str for a in assignments)
                        if not already_tracked:
                            assignments.append({
                                "worker_id": worker_id_str,
                                "confirmed": False,
                                "arrived": False,
                                "paid": False,
                                "completed": False,
                                "rating": None,
                                "accepted_at": time.time()
                            })
                        new_status = 'in_progress' if len(accepted_workers) < required else 'filled'
                        tasks_collection.update_one(
                            {"_id": task['_id']},
                            {
                                "$set": {
                                    "accepted_workers": accepted_workers,
                                    "status": new_status,
                                    "worker_assignments": assignments
                                },
                                "$pull": {"denied_by": worker_id_str}
                            }
                        )
                        
                        # Create notification for farmers
                        try:
                            worker_doc = workers_collection.find_one({"_id": ObjectId(session['worker_id'])})
                            worker_name = worker_doc.get('name', 'Unknown Worker') if worker_doc else 'Unknown Worker'
                            worker_phone = worker_doc.get('phone', 'Not provided') if worker_doc else 'Not provided'

                            farmer_id_for_notification = None
                            # Case 1: help_request-based task has explicit farmer_id
                            if task.get('task_type') == 'help_request' and task.get('farmer_id'):
                                farmer_id_for_notification = task.get('farmer_id')
                            else:
                                # Case 2: task posted directly by a farmer (posted_by refers to a farmer id)
                                try:
                                    possible_farmer_id = task.get('posted_by')
                                    if possible_farmer_id:
                                        possible_farmer = farmers_collection.find_one({"_id": ObjectId(possible_farmer_id)})
                                        if possible_farmer:
                                            farmer_id_for_notification = possible_farmer_id
                                except Exception:
                                    farmer_id_for_notification = None

                            if farmer_id_for_notification:
                                notification_data = {
                                    "farmer_id": farmer_id_for_notification,
                                    "worker_id": session['worker_id'],
                                    "task_id": str(task['_id']),
                                    "help_request_id": task.get('help_request_id'),
                                    "message": f"Your task has been accepted by worker: {worker_name}",
                                    "worker_name": worker_name,
                                    "worker_phone": worker_phone,
                                    "task_location": task.get('location'),
                                    "created_at": time.time(),
                                    "read": False
                                }
                                notifications_collection.insert_one(notification_data)
                                logger.info(f"Notification created for farmer {farmer_id_for_notification} about worker {worker_name} accepting task {task.get('task')}")
                        except Exception as e:
                            logger.error(f"Error creating notification: {e}")
                        
                        task_name = task.get('task')
                        task_location = (task.get('location') or '').capitalize()
                        message = translate_text(
                            lang,
                            f"You accepted the '{task_name}' task at {task_location}",
                            f"మీరు '{task_name}' పనిని {task_location} వద్ద అంగీకరించారు"
                        )
                        message_type = 'success'
                elif action == 'deny':
                    worker_id_str = session['worker_id']
                    tasks_collection.update_one(
                        {"_id": task['_id']},
                        {
                            "$addToSet": {"denied_by": worker_id_str},
                            "$pull": {
                                "accepted_workers": worker_id_str,
                                "worker_assignments": {"worker_id": worker_id_str}
                            }
                        }
                    )
                    task_name = task.get('task')
                    message = translate_text(
                        lang,
                        f"You denied the '{task_name}' task",
                        f"మీరు '{task_name}' పనిని తిరస్కరించారు"
                    )
                    message_type = 'info'
                elif action == 'complete':
                    accepted_workers = task.get('accepted_workers') or []
                    if session['worker_id'] not in accepted_workers:
                        message = translate_text(lang, "You cannot complete a task you didn't accept", "మీరు అంగీకరించని పనిని పూర్తి చేయలేరు")
                        message_type = 'warning'
                    elif task.get('completed'):
                        message = translate_text(lang, "Task already marked as complete", "ఈ పని ఇప్పటికే పూర్తిగా గుర్తించబడింది")
                        message_type = 'info'
                    else:
                        worker_id_str = session['worker_id']
                        ensure_worker_assignment_entry(task['_id'], worker_id_str)
                        tasks_collection.update_one(
                            {"_id": task['_id']},
                            {
                                "$set": {
                                    "completed": True,
                                    "completed_at": time.time(),
                                    "status": "completed",
                                    "worker_assignments.$[assignment].completed": True
                                }
                            },
                            array_filters=[{"assignment.worker_id": worker_id_str}]
                        )
                        task_name = task.get('task')
                        message = translate_text(
                            lang,
                            f"Marked '{task_name}' as completed",
                            f"'{task_name}' పనిని పూర్తిగా గుర్తించారు"
                        )
                        message_type = 'success'
            except Exception as e:
                logger.error(f"Error processing task action {action} for task {task_id}: {e}")
                message = translate_text(lang, f"Error: {str(e)}", f"లోపం: {str(e)}")
                message_type = 'error'

    needs_refresh = (request.method == 'GET') or (action in ('fetch_tasks', 'accept', 'deny', 'complete'))
    if needs_refresh:
        nearby_tasks, fetch_message, fetch_type = load_nearby_tasks()
        if message is None and fetch_message is not None:
            message = fetch_message
            message_type = fetch_type

    # Fetch accepted tasks with accepter names and completion status
    accepted_tasks_raw = list(tasks_collection.find({"accepted_workers": {"$in": [session['worker_id']]}}).sort("created_at", -1))
    accepted_tasks = []
    for task in accepted_tasks_raw:
        accepter_ids = task.get('accepted_workers') or []
        accepter_names = []
        for wid in accepter_ids:
            try:
                w = workers_collection.find_one({"_id": ObjectId(wid)})
                if w:
                    accepter_names.append(w.get('name', 'Unknown'))
            except Exception:
                continue
        task['accepter_name'] = ", ".join(accepter_names) if accepter_names else "Unknown"
        assignment_info = {}
        for entry in task.get('worker_assignments', []) or []:
            if entry.get('worker_id') == session['worker_id']:
                assignment_info = entry
                break
        task['assignment_status'] = assignment_info or {}
        accepted_tasks.append(task)

    completed_tasks = []
    completed_cursor = tasks_collection.find({
        "worker_assignments": {
            "$elemMatch": {
                "worker_id": session['worker_id'],
                "completed": True
            }
        }
    }).sort("completed_at", -1).limit(5)

    for task in completed_cursor:
        assignment_info = {}
        for entry in task.get('worker_assignments', []) or []:
            if entry.get('worker_id') == session['worker_id']:
                assignment_info = entry
                break
        completed_tasks.append({
            "task": task.get('task'),
            "location": task.get('location'),
            "wage": task.get('wage'),
            "completed_at": task.get('completed_at'),
            "assignment_status": assignment_info,
            "task_id": str(task.get('_id'))
        })

    try:
        message_query = {"worker_id": str(session['worker_id']), "role": "worker"}
        latest_messages = list(notifications_collection.find(message_query).sort("created_at", -1).limit(5))
        if not latest_messages and worker.get('phone'):
            latest_messages = list(notifications_collection.find({
                "worker_phone": worker.get('phone'),
                "role": "worker"
            }).sort("created_at", -1).limit(5))
    except Exception as e:
        logger.error(f"Error fetching worker messages: {e}")

    logger.info(f"Rendering worker dashboard for {worker['name']} with {len(accepted_tasks)} accepted tasks")
    help_modal = session.pop('worker_help_modal', None)

    return render_template(
        'worker.html',
        nearby_tasks=nearby_tasks,
        accepted_tasks=accepted_tasks,
        completed_tasks=completed_tasks,
        worker=worker,
        message=message,
        message_type=message_type,
        latest_messages=latest_messages,
        lang=lang,
        translate=translator,
        flash_labels=flash_labels,
        flash_default=flash_default,
        help_modal=help_modal
    )

@app.route('/logout')
def logout():
    logger.info("User logging out, clearing session")
    lang = session.get('preferred_lang', 'en')
    session.pop('_flashes', None)
    session.clear()
    session.modified = True
    return redirect(url_for('get_started', lang=lang))

@app.route('/admin', methods=['GET', 'POST'])
def admin():
    # Aggregate metrics for overview panels
    total_farmers = farmers_collection.count_documents({})
    total_workers = workers_collection.count_documents({})
    total_volunteers = volunteers_collection.count_documents({})

    total_tasks = tasks_collection.count_documents({})
    open_tasks = tasks_collection.count_documents({"status": "open"})
    completed_tasks = tasks_collection.count_documents({"status": "completed"})
    in_progress_tasks = tasks_collection.count_documents({"status": "in_progress"})

    recent_tasks = list(tasks_collection.find().sort("created_at", -1).limit(5))
    recent_requests = list(requests_collection.find().sort("created_at", -1).limit(5))
    recent_volunteers = list(volunteers_collection.find().sort("_id", -1).limit(5))

    village_distribution = list(tasks_collection.aggregate([
        {"$group": {"_id": "$village", "count": {"$sum": 1}}},
        {"$sort": {"count": -1}},
        {"$limit": 6}
    ]))

    rating_aggregate = list(workers_collection.aggregate([
        {"$match": {"rating_count": {"$gt": 0}}},
        {"$group": {"_id": None, "total": {"$sum": "$rating_total"}, "count": {"$sum": "$rating_count"}}}
    ]))
    ratings_avg = None
    ratings_count = 0
    if rating_aggregate:
        summary = rating_aggregate[0]
        total_reviews = summary.get('count', 0)
        total_score = summary.get('total', 0)
        if total_reviews:
            ratings_avg = round(total_score / total_reviews, 1)
            ratings_count = total_reviews

    return render_template(
        'admin.html',
        stats={
            "farmers": total_farmers,
            "workers": total_workers,
            "volunteers": total_volunteers,
            "tasks": total_tasks,
            "open_tasks": open_tasks,
            "completed_tasks": completed_tasks,
            "in_progress_tasks": in_progress_tasks,
            "ratings_avg": ratings_avg,
            "ratings_count": ratings_count
        },
        recent_tasks=recent_tasks,
        recent_requests=recent_requests,
        recent_volunteers=recent_volunteers,
        village_distribution=village_distribution,
        tasks=task_labels,
        villages=villages
    )

@app.route('/assign_task', methods=['POST'])
def assign_task():
    if 'farmer_id' not in session:
        flash("Please login as a farmer.", "warning")
        return redirect('/farmer_login')

    task_id = request.form.get('task_id')
    worker_phone = request.form.get('worker_phone', '').strip()

    if not task_id or not worker_phone:
        flash("Task and worker phone are required.", "warning")
        return redirect('/farmer')

    try:
        task = tasks_collection.find_one({"_id": ObjectId(task_id), "posted_by": session['farmer_id']})
        if not task:
            flash("Task not found or not owned by you.", "danger")
            return redirect('/farmer')

        if task.get('status') not in ['open', 'in_progress']:
            flash("Task cannot be assigned in its current status.", "warning")
            return redirect('/farmer')

        accepted_workers = task.get('accepted_workers') or []
        required = int(task.get('num_workers', 1)) if str(task.get('num_workers', '')).isdigit() else 1
        if len(accepted_workers) >= required:
            flash("Task already has required number of workers.", "info")
            return redirect('/farmer')

        worker = workers_collection.find_one({"phone": worker_phone})
        if not worker:
            flash("Worker with this phone not found.", "danger")
            return redirect('/farmer')

        tasks_collection.update_one(
            {"_id": task['_id']},
            {"$set": {"assigned_to": str(worker['_id'])}}
        )

        flash(f"Task assigned to {worker.get('name', 'worker')} ({worker_phone}).", "success")
        return redirect('/farmer')
    except Exception as e:
        logger.error(f"Error assigning task: {e}")
        flash(f"Error assigning task: {str(e)}", "danger")
        return redirect('/farmer')

if __name__ == '__main__':
    debug_mode = os.getenv('FLASK_DEBUG', '').lower() in ('1', 'true', 'yes')
    port = int(os.getenv('FLASK_RUN_PORT', 5001))
    app.run(debug=debug_mode, port=port)
