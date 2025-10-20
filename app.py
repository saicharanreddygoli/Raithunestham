from flask import Flask, request, render_template, jsonify, redirect, session, url_for, flash
from geopy.geocoders import Nominatim
import logging
import json
import os
import time
import math
from retrying import retry
from pymongo import MongoClient
from pymongo.errors import ConnectionFailure
from bson import ObjectId
from werkzeug.security import generate_password_hash, check_password_hash
from datetime import datetime
from dotenv import load_dotenv
from pathlib import Path

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

# Jinja filter to render Unix timestamps in templates
@app.template_filter('timestamp_to_datetime')
def timestamp_to_datetime_filter(timestamp):
    try:
        if isinstance(timestamp, (int, float)):
            return datetime.fromtimestamp(timestamp).strftime('%Y-%m-%d %H:%M')
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
                return render_template('farmer_login.html', error="Please enter both name and password", lang=lang)
           
            farmer = farmers_collection.find_one({"name": name})
           
            if not farmer:
                logger.warning(f"Farmer not found: {name}")
                return render_template('farmer_login.html', error="Farmer not found. Please check your name or register first.", lang=lang)
           
            if not check_password_hash(farmer['password'], password):
                logger.warning(f"Invalid password for farmer: {name}")
                return render_template('farmer_login.html', error="Invalid password. Please try again.", lang=lang)
           
            session['farmer_id'] = str(farmer['_id'])
            logger.info(f"Farmer {name} logged in successfully, redirecting to dashboard")
            flash("Farmer login successful! Welcome to the dashboard.", "success")
            return redirect('/farmer')
        except Exception as e:
            logger.error(f"Error in farmer login: {e}")
            return render_template('farmer_login.html', error=f"Login error: {str(e)}", lang=lang)
   
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
            return render_template('farmer_reg.html', error="All fields required", villages=villages, lang=lang)
        if farmers_collection.find_one({"name": name}):
            return render_template('farmer_reg.html', error="Name already exists", villages=villages, lang=lang)
        if village not in [v.lower() for v in villages]:
            return render_template('farmer_reg.html', error="Invalid village", villages=villages, lang=lang)
        try:
            farmer_data = {
                "name": name,
                "password": generate_password_hash(password),
                "phone": phone,
                "village": village,
                "location": f"{village}, gudlavalleru"
            }
            result = farmers_collection.insert_one(farmer_data)
            session['farmer_id'] = str(result.inserted_id)
            logger.info(f"Farmer {name} registered successfully, redirecting to dashboard")
            flash("Farmer registration completed successfully!", "success")
            return redirect('/farmer')
        except Exception as e:
            logger.error(f"Error registering farmer {name}: {e}")
            return render_template('farmer_reg.html', error=f"Registration failed: {str(e)}", villages=villages, lang=lang)
   
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
                            result = tasks_collection.insert_one(task_data)
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
   
    # If fetch help request status requested
    if request.method == 'POST' and request.form.get('action') == 'fetch_help_status':
        # Get help requests sent by this farmer
        help_requests = list(requests_collection.find({"farmer_id": session['farmer_id']}).sort("created_at", -1))
        accepted_overview = []
        
        for help_req in help_requests:
            if help_req.get('status') == 'accepted':
                # Find the task created for this help request
                task = tasks_collection.find_one({"help_request_id": str(help_req['_id'])})
                if task:
                    accepter_ids = task.get('accepted_workers') or []
                    accepters = []
                    for wid in accepter_ids:
                        try:
                            w = workers_collection.find_one({"_id": ObjectId(wid)})
                            if w:
                                phone = w.get('phone', '')
                                # If phone is empty or None, show a more helpful message
                                if not phone or phone.strip() == '':
                                    phone = 'Phone not available'
                                
                                accepters.append({
                                    "name": w.get('name', 'Unknown'),
                                    "village": w.get('village', ''),
                                    "location": w.get('location', ''),
                                    "phone": phone
                                })
                                logger.info(f"Worker {w.get('name')} phone: {phone}")
                        except Exception as e:
                            logger.error(f"Error retrieving worker {wid}: {e}")
                            continue
                    
                    # Get volunteer info
                    volunteer = volunteers_collection.find_one({"_id": ObjectId(help_req['volunteer_id'])})
                    volunteer_name = volunteer.get('name', 'Unknown Volunteer') if volunteer else 'Unknown Volunteer'
                    
                    accepted_overview.append({
                        "task": task.get('task'),
                        "location": task.get('location'),
                        "num_workers": task.get('num_workers'),
                        "accepted": accepters,
                        "volunteer_name": volunteer_name,
                        "help_request_id": str(help_req['_id']),
                        "status": help_req.get('status')
                    })
    
    # If fetch notifications requested
    if request.method == 'POST' and request.form.get('action') == 'fetch_notifications':
        notifications = list(notifications_collection.find({"farmer_id": session['farmer_id']}).sort("created_at", -1).limit(10))
        logger.info(f"Fetched {len(notifications)} notifications for farmer {farmer['name']}")

    # Fetch farmer's own open tasks for assignment UI
    try:
        my_open_tasks = list(tasks_collection.find({
            "posted_by": session['farmer_id'],
            "status": {"$in": ["open", "in_progress"]}
        }).sort("created_at", -1))
    except Exception as e:
        logger.error(f"Error fetching farmer's open tasks: {e}")

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
    # Allow viewing the list without login; require login only for sending requests
    farmer = None
    if 'farmer_id' in session:
        farmer = farmers_collection.find_one({"_id": ObjectId(session['farmer_id'])})

    if request.method == 'POST':
        if 'farmer_id' not in session:
            flash("Please login as a farmer to send a request.", "warning")
            return redirect('/farmer_login')
        volunteer_id = request.form.get('volunteer_id')
        if volunteer_id:
            request_data = {
                "farmer_id": session['farmer_id'],
                "volunteer_id": volunteer_id,
                "request_type": "farmer_help",
                "status": "pending",
                "created_at": time.time()
            }
            requests_collection.insert_one(request_data)
            flash("Request sent to volunteer!", "success")
            return redirect('/farmer')
    
    volunteers_by_village = {}
    for v in villages:
        volunteer = volunteers_collection.find_one({"village": v})
        if volunteer:
            volunteers_by_village[v] = volunteer
    
    return render_template('farmer_help.html', volunteers_by_village=volunteers_by_village, villages=villages, farmer=farmer)

@app.route('/worker_help', methods=['GET', 'POST'])
def worker_help():
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
            errors.append("Please provide your name and phone number.")
        if not volunteer_id:
            errors.append("Please select a volunteer to contact.")
        if worker_village and worker_village not in [v.lower() for v in villages]:
            errors.append("Please select a valid village.")

        volunteer_doc = None
        if volunteer_id:
            try:
                volunteer_doc = volunteers_collection.find_one({"_id": ObjectId(volunteer_id)})
                if not volunteer_doc:
                    errors.append("Selected volunteer was not found.")
            except Exception:
                errors.append("Selected volunteer is invalid.")

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
            flash("Help request sent to volunteer! They will reach out to you soon.", "success")
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
        prefill_task_id=prefill_task_id
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
                return render_template('volunteer_login.html', error="Please enter both name and password", lang=lang)
           
            volunteer = volunteers_collection.find_one({"name": name})
           
            if not volunteer:
                logger.warning(f"Volunteer not found: {name}")
                return render_template('volunteer_login.html', error="Volunteer not found. Please check your name or register first.", lang=lang)
           
            if not check_password_hash(volunteer['password'], password):
                logger.warning(f"Invalid password for volunteer: {name}")
                return render_template('volunteer_login.html', error="Invalid password. Please try again.", lang=lang)
           
            session['volunteer_id'] = str(volunteer['_id'])
            logger.info(f"Volunteer {name} logged in successfully, redirecting to dashboard")
            flash("Volunteer login successful! Welcome to the dashboard.", "success")
            return redirect('/volunteer')
        except Exception as e:
            logger.error(f"Error in volunteer login: {e}")
            return render_template('volunteer_login.html', error=f"Login error: {str(e)}", lang=lang)
   
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
            return render_template('volunteer_reg.html', error="All fields required", villages=villages, lang=lang)
        if volunteers_collection.find_one({"name": name}):
            return render_template('volunteer_reg.html', error="Name already exists", villages=villages, lang=lang)
        if village not in [v.lower() for v in villages]:
            return render_template('volunteer_reg.html', error="Invalid village", villages=villages, lang=lang)
        try:
            volunteer_data = {
                "name": name,
                "password": generate_password_hash(password),
                "phone": phone,
                "village": village
            }
            result = volunteers_collection.insert_one(volunteer_data)
            session['volunteer_id'] = str(result.inserted_id)
            logger.info(f"Volunteer {name} registered successfully, redirecting to dashboard")
            flash("Volunteer registration completed successfully!", "success")
            return redirect('/volunteer')
        except Exception as e:
            logger.error(f"Error registering volunteer {name}: {e}")
            return render_template('volunteer_reg.html', error=f"Registration failed: {str(e)}", villages=villages, lang=lang)
   
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
                        message = "Request declined."
                        message_type = 'info'
                    else:
                        message = "Request not found or already processed."
                        message_type = 'warning'
                except Exception as e:
                    logger.error(f"Error declining request {help_request_id}: {e}")
                    message = f"Error processing request: {str(e)}"
                    message_type = 'error'
            else:
                message = "Invalid request ID"
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
                message = "Request not found or already processed."
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
                        message = "Farmer details not available for this request."
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
                                errors.append("Wage cannot be negative.")
                        except ValueError:
                            errors.append("Enter a valid wage amount.")
                            wage_value = 0.0

                        try:
                            workers_value = int(workers_input) if workers_input else 2
                            if workers_value < 1 or workers_value > 50:
                                errors.append("Number of workers must be between 1 and 50.")
                        except ValueError:
                            errors.append("Enter a valid number of workers.")
                            workers_value = 2

                        if task_village_input not in [v.lower() for v in villages]:
                            errors.append("Please choose a valid village for the task.")

                        if errors:
                            message = errors[0]
                            message_type = 'error'
                        else:
                            location = f"{task_village_input}, gudlavalleru"
                            coordinates = get_coordinates(location, task_village_input)
                            if not coordinates:
                                message = "Could not find coordinates for the selected village."
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
                                        message = f"Task '{task_name_input}' posted for {task_village_input.capitalize()}. Workers will now see it."
                                        message_type = 'success'
                                        logger.info(f"Volunteer {volunteer['name']} created task {task_name_input} for request {help_request_id}")
                                    else:
                                        message = "Request accepted but failed to create worker task. Please try again."
                                        message_type = 'warning'
                                except Exception as e:
                                    logger.error(f"Error creating task for help request {help_request_id}: {e}")
                                    message = f"Error creating task: {str(e)}"
                                    message_type = 'error'

                elif request_type == 'worker_help':
                    worker_name = help_request.get('worker_name', 'Worker')
                    worker_phone = (help_request.get('worker_phone') or '').strip()
                    worker_village = help_request.get('worker_village')
                    task_details = help_request.get('task_details') or {}
                    task_name = task_details.get('task_name') or 'Not specified'
                    task_location = task_details.get('task_location') or 'Not specified'
                    task_wage = task_details.get('task_wage') or '—'

                    message = f"Request accepted! Please contact {worker_name}"
                    if worker_phone:
                        message += f" at {worker_phone}"
                    if worker_village:
                        message += f" from {str(worker_village).capitalize()}"
                    notes = help_request.get('worker_notes')
                    message += "."
                    if notes:
                        message += f" Note: {notes}"
                    message += f" Task: {task_name} at {task_location} (₹{task_wage}/day)."

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
                    message = "Request accepted."
                    message_type = 'success'

        elif action == 'create_task':
            village = (request.form.get('village') or '').strip().lower()
            task = (request.form.get('task') or '').strip()
            wage = (request.form.get('wage') or '').strip()
            num_workers = (request.form.get('num_workers') or '').strip()
            description = (request.form.get('description') or '').strip()

            logger.info(f"Volunteer {volunteer['name']} attempting to post task: {task} in {village}, gudlavalleru")

            if not all([village, task]):
                message = "All required fields must be filled"
                message_type = 'error'
            elif village not in [v.lower() for v in villages]:
                message = "Please select a valid village"
                message_type = 'error'
            elif task not in task_labels:
                message = "Please select a valid task type"
                message_type = 'error'
            else:
                try:
                    wage_value = float(wage) if wage else 0.0
                    num_workers_value = int(num_workers) if num_workers else 1

                    if wage_value < 0:
                        message = "Wage cannot be negative"
                        message_type = 'error'
                    elif num_workers_value < 1 or num_workers_value > 50:
                        message = "Number of workers must be between 1 and 50"
                        message_type = 'error'
                    else:
                        location = f"{village}, gudlavalleru"
                        coordinates = get_coordinates(location, village)

                        if not coordinates:
                            message = "Could not find coordinates for the specified village."
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
                                message = f"A similar {task} task already exists for {location.capitalize()}"
                                message_type = 'warning'
                            else:
                                result = tasks_collection.insert_one(task_data)
                                if result.inserted_id:
                                    message = f"✅ Task '{task}' successfully posted for {location.capitalize()} with wage ₹{wage_value}/day for {num_workers_value} worker(s)"
                                    message_type = 'success'
                                    logger.info(f"Task {task} posted successfully for {location} by volunteer {session['volunteer_id']}")
                                else:
                                    message = "Failed to post task. Please try again."
                                    message_type = 'error'
                except ValueError as e:
                    message = "Invalid wage or number of workers format"
                    message_type = 'error'
                    logger.error(f"Value error in task posting: {e}")
                except Exception as e:
                    message = f"An error occurred: {str(e)}"
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
            help_request['worker_details'] = {
                'name': help_request.get('worker_name', 'Worker'),
                'phone': help_request.get('worker_phone', ''),
                'village': help_request.get('worker_village', ''),
                'notes': help_request.get('worker_notes', '')
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
        lang=lang
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
                return render_template('worker_login.html', error="Please enter both name and password", lang=lang)
           
            worker = workers_collection.find_one({"name": name})
           
            if not worker:
                logger.warning(f"Worker not found: {name}")
                return render_template('worker_login.html', error="Worker not found. Please check your name or register first.", lang=lang)
           
            if not check_password_hash(worker['password'], password):
                logger.warning(f"Invalid password for worker: {name}")
                return render_template('worker_login.html', error="Invalid password. Please try again.", lang=lang)
           
            session['worker_id'] = str(worker['_id'])
            logger.info(f"Worker {name} logged in successfully, redirecting to dashboard")
            flash("Worker login successful! Welcome to the dashboard.", "success")
            return redirect('/worker')
        except Exception as e:
            logger.error(f"Error in worker login: {e}")
            return render_template('worker_login.html', error=f"Login error: {str(e)}", lang=lang)
   
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
        location = f"{village}, gudlavalleru"
        if not all([name, password, phone, village]):
            return render_template('worker_reg.html', error="All fields required", villages=villages, lang=lang)
        if workers_collection.find_one({"name": name}):
            return render_template('worker_reg.html', error="Name already exists", villages=villages, lang=lang)
        if village not in [v.lower() for v in villages]:
            return render_template('worker_reg.html', error="Invalid village", villages=villages, lang=lang)
        coordinates = get_coordinates(location, village)
        if not coordinates:
            return render_template('worker_reg.html', error="Could not find location", villages=villages, lang=lang)
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
                "denied_tasks": []
            }
            result = workers_collection.insert_one(worker_data)
            session['worker_id'] = str(result.inserted_id)
            logger.info(f"Worker {name} registered successfully, redirecting to dashboard")
            flash("Worker registration completed successfully!", "success")
            return redirect('/worker')
        except Exception as e:
            logger.error(f"Error registering worker {name}: {e}")
            return render_template('worker_reg.html', error=f"Registration failed: {str(e)}", villages=villages, lang=lang)
   
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

    lang = session.get('preferred_lang', 'en').lower()
    if lang not in ('en', 'te'):
        lang = 'en'

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
                        flash("Notification removed.", "success")
                    else:
                        flash("Notification not found or already removed.", "warning")
                except Exception as err:
                    flash("Could not delete notification.", "danger")
                    logger.error(f"Error deleting worker notification {notification_id}: {err}")
            else:
                flash("Invalid notification selected.", "warning")
            return redirect('/worker')
   
    nearby_tasks = []
    message = None
    message_type = 'info'
    latest_messages = []
   
    # Automatically fetch tasks whenever the dashboard loads via GET
    should_fetch_on_get = request.method == 'GET'
    if (request.method == 'POST' and request.form.get('action') == 'fetch_tasks') or should_fetch_on_get:
        try:
            worker_coords = worker['coordinates']['coordinates']
            worker_lon, worker_lat = worker_coords
           
            logger.info(f"Worker {worker['name']} at {worker['location']} fetching tasks (coords: {worker_coords})")
           
            all_tasks = list(tasks_collection.find({
                "status": {"$in": ["open", "in_progress", "filled"]},
                "denied_by": {"$nin": [session['worker_id']]}
            }).sort("created_at", -1))
           
            logger.info(f"Found {len(all_tasks)} open tasks in database")
           
            nearby_tasks = []
            for task in all_tasks:
                if 'coordinates' in task and 'coordinates' in task['coordinates']:
                    task_coords = task['coordinates']['coordinates']
                    task_lon, task_lat = task_coords
                    
                    # Hide if worker already accepted or capacity reached
                    accepted_workers = task.get('accepted_workers') or []
                    try:
                        required = int(task.get('num_workers', 1))
                    except Exception:
                        required = 1
                    if session['worker_id'] in accepted_workers:
                        continue
                    if len(accepted_workers) >= required:
                        continue

                    # If task is specifically assigned to a worker, only show to that worker
                    assigned_to = task.get('assigned_to')
                    if assigned_to and assigned_to != session['worker_id']:
                        continue

                    distance = haversine_distance(worker_lat, worker_lon, task_lat, task_lon)
                    if distance is not None and distance <= 50.0:  # Ensure distance is calculated
                        task['distance_km'] = round(distance, 2)
                        nearby_tasks.append(task)
                        logger.info(f"Task {task['task']} at {task['location']} added to nearby tasks (distance: {distance:.2f} km, coords: {task_coords})")
           
            nearby_tasks.sort(key=lambda x: x.get('distance_km', 999))
           
            # Remove duplicates based on task ID to ensure no duplicate tasks
            unique_tasks = {}
            for task in nearby_tasks:
                task_id = str(task['_id'])
                if task_id not in unique_tasks:
                    unique_tasks[task_id] = task
            nearby_tasks = list(unique_tasks.values())
           
            if nearby_tasks:
                message = f"Found {len(nearby_tasks)} nearby tasks within 50km radius"
                message_type = 'success'
            else:
                message = "No tasks found within 50km radius. Try checking later."
                message_type = 'info'
               
        except Exception as e:
            logger.error(f"Error fetching tasks: {e}")
            message = f"Error fetching tasks: {str(e)}"
            message_type = 'error'
   
    # Handle task actions: accept, deny, complete
    if request.method == 'POST':
        action = request.form.get('action')
        task_id = request.form.get('task_id')
        if action in ['accept', 'deny', 'complete'] and task_id:
            try:
                task = tasks_collection.find_one({"_id": ObjectId(task_id)})
                if not task:
                    message = "Task not found"
                    message_type = 'error'
                elif action == 'accept':
                    accepted_workers = task.get('accepted_workers') or []
                    try:
                        required = int(task.get('num_workers', 1))
                    except Exception:
                        required = 1
                    if session['worker_id'] in accepted_workers:
                        message = "You already accepted this task"
                        message_type = 'info'
                    elif len(accepted_workers) >= required:
                        message = "This task is no longer available"
                        message_type = 'warning'
                    else:
                        accepted_workers.append(session['worker_id'])
                        new_status = 'in_progress' if len(accepted_workers) < required else 'filled'
                        tasks_collection.update_one(
                            {"_id": task['_id']},
                            {"$set": {"accepted_workers": accepted_workers, "status": new_status}, "$pull": {"denied_by": session['worker_id']}}
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
                        
                        message = f"You accepted the '{task.get('task')}' task at {task.get('location').capitalize()}"
                        message_type = 'success'
                elif action == 'deny':
                    tasks_collection.update_one({"_id": task['_id']}, {"$addToSet": {"denied_by": session['worker_id']}})
                    message = f"You denied the '{task.get('task')}' task"
                    message_type = 'info'
                elif action == 'complete':
                    accepted_workers = task.get('accepted_workers') or []
                    if session['worker_id'] not in accepted_workers:
                        message = "You cannot complete a task you didn't accept"
                        message_type = 'warning'
                    elif task.get('completed'):
                        message = "Task already marked as complete"
                        message_type = 'info'
                    else:
                        tasks_collection.update_one(
                            {"_id": task['_id']},
                            {"$set": {"completed": True, "completed_at": time.time(), "status": "completed"}}
                        )
                        message = f"Marked '{task.get('task')}' as completed"
                        message_type = 'success'
            except Exception as e:
                logger.error(f"Error processing task action {action} for task {task_id}: {e}")
                message = f"Error: {str(e)}"
                message_type = 'error'

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
        accepted_tasks.append(task)
   
    try:
        message_query = {"worker_id": str(session['worker_id']), "role": "worker"}
        latest_messages = list(notifications_collection.find(message_query).sort("created_at", -1).limit(3))
        if not latest_messages and worker.get('phone'):
            latest_messages = list(notifications_collection.find({
                "worker_phone": worker.get('phone'),
                "role": "worker"
            }).sort("created_at", -1).limit(3))
    except Exception as e:
        logger.error(f"Error fetching worker messages: {e}")

    logger.info(f"Rendering worker dashboard for {worker['name']} with {len(accepted_tasks)} accepted tasks")
    return render_template(
        'worker.html',
        nearby_tasks=nearby_tasks,
        accepted_tasks=accepted_tasks,
        worker=worker,
        message=message,
        message_type=message_type,
        latest_messages=latest_messages,
        lang=lang
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

    return render_template(
        'admin.html',
        stats={
            "farmers": total_farmers,
            "workers": total_workers,
            "volunteers": total_volunteers,
            "tasks": total_tasks,
            "open_tasks": open_tasks,
            "completed_tasks": completed_tasks,
            "in_progress_tasks": in_progress_tasks
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
