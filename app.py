from flask import Flask, request, render_template, jsonify, redirect, session, url_for, flash
from transformers import BertTokenizer, BertForSequenceClassification
import torch
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

app = Flask(__name__)
app.secret_key = 'secret_key_2025'

# Set up logging
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

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
    mongo_client = MongoClient('mongodb+srv://saicharanreddygoli3:9BqqD7HdC8kMJyTS@cluster0.ay21zex.mongodb.net/raithunestham?retryWrites=true&w=majority')
    db = mongo_client['raithunestham']
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
    
    # Simple cleanup of duplicate tasks
    try:
        # Find all open tasks
        all_tasks = list(tasks_collection.find({"status": "open"}))
        
        # Group by location, task, and posted_by
        task_groups = {}
        for task in all_tasks:
            key = f"{task['location']}_{task['task']}_{task['posted_by']}"
            if key not in task_groups:
                task_groups[key] = []
            task_groups[key].append(task)
        
        # Remove duplicates (keep the most recent one)
        removed_count = 0
        for key, tasks in task_groups.items():
            if len(tasks) > 1:
                # Sort by created_at descending (most recent first)
                tasks.sort(key=lambda x: x.get('created_at', 0), reverse=True)
                # Keep the first (most recent), remove the rest
                for task in tasks[1:]:
                    tasks_collection.delete_one({"_id": task["_id"]})
                    removed_count += 1
                    logger.info(f"Removed duplicate task: {task['task']} at {task['location']}")
        
        if removed_count > 0:
            logger.info(f"Cleaned up {removed_count} duplicate tasks")
    except Exception as e:
        logger.warning(f"Could not clean up duplicates: {e}")
    
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

# Load pretrained BERT model and tokenizer
model_name = "bert-base-uncased"
tokenizer = BertTokenizer.from_pretrained(model_name)
model = BertForSequenceClassification.from_pretrained(model_name, num_labels=4)

# Available tasks
task_labels = ["Sowing", "Weeding", "Harvesting", "Irrigation"]

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

# Fine-tune BERT
def fine_tune_bert():
    optimizer = torch.optim.Adam(model.parameters(), lr=2e-5)
    model.train()
    for text, label in [("gudlavalleru", 0)]:
        inputs = tokenizer(text, return_tensors="pt", padding=True, truncation=True, max_length=32)
        labels = torch.tensor([label])
        outputs = model(**inputs, labels=labels)
        loss = outputs.loss
        loss.backward()
        optimizer.step()
        optimizer.zero_grad()
    model.eval()
    logger.info("BERT fine-tuned")

fine_tune_bert()
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
    inputs = tokenizer("gudlavalleru, andhra pradesh", return_tensors="pt", padding=True, truncation=True, max_length=32)
    with torch.no_grad():
        outputs = model(**inputs)
    predicted_task_idx = torch.argmax(outputs.logits, dim=1).item()
    logger.info(f"Predicted task for {location}: {task_labels[predicted_task_idx]}")
    return jsonify({"task": task_labels[predicted_task_idx]})

@app.route('/farmer_login', methods=['GET', 'POST'])
def farmer_login():
    if request.method == 'POST':
        try:
            name = request.form.get('name', '').strip()
            password = request.form.get('password', '').strip()
           
            logger.info(f"Farmer login attempt for: {name}")
           
            if not name or not password:
                return render_template('farmer_login.html', error="Please enter both name and password")
           
            farmer = farmers_collection.find_one({"name": name})
           
            if not farmer:
                logger.warning(f"Farmer not found: {name}")
                return render_template('farmer_login.html', error="Farmer not found. Please check your name or register first.")
           
            if not check_password_hash(farmer['password'], password):
                logger.warning(f"Invalid password for farmer: {name}")
                return render_template('farmer_login.html', error="Invalid password. Please try again.")
           
            session['farmer_id'] = str(farmer['_id'])
            logger.info(f"Farmer {name} logged in successfully, redirecting to dashboard")
            flash("Farmer login successful! Welcome to the dashboard.", "success")
            return redirect('/farmer')
        except Exception as e:
            logger.error(f"Error in farmer login: {e}")
            return render_template('farmer_login.html', error=f"Login error: {str(e)}")
   
    logger.info("Rendering farmer login page")
    return render_template('farmer_login.html')

@app.route('/farmer_reg', methods=['GET', 'POST'])
def farmer_reg():
    if request.method == 'POST':
        name = request.form.get('name', '').strip()
        password = request.form.get('password', '').strip()
        phone = request.form.get('phone', '').strip()
        village = request.form.get('village', '').strip().lower()
        
        if not all([name, password, phone, village]):
            return render_template('farmer_reg.html', error="All fields required", villages=villages)
        if farmers_collection.find_one({"name": name}):
            return render_template('farmer_reg.html', error="Name already exists", villages=villages)
        if village not in [v.lower() for v in villages]:
            return render_template('farmer_reg.html', error="Invalid village", villages=villages)
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
            logger.info(f"Farmer {name} registered successfully, redirecting to home")
            flash("Farmer registration completed successfully!", "success")
            return redirect('/')
        except Exception as e:
            logger.error(f"Error registering farmer {name}: {e}")
            return render_template('farmer_reg.html', error=f"Registration failed: {str(e)}", villages=villages)
   
    logger.info("Rendering farmer registration page")
    return render_template('farmer_reg.html', villages=villages)

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
   
    if request.method == 'POST':
        mandal = request.form.get('mandal', '').strip().lower()
        village = request.form.get('village', '').strip().lower()
        task = request.form.get('task', '').strip()
        wage = request.form.get('wage', '').strip()
        num_workers = request.form.get('num_workers', '').strip()
        description = request.form.get('description', '').strip()
       
        logger.info(f"Farmer {farmer['name']} attempting to post task: {task} in {village}, {mandal}")
       
        if not all([mandal, village, task]):
            message = "All required fields must be filled"
            message_type = 'error'
        elif mandal != 'gudlavalleru':
            message = "Please select a valid mandal"
            message_type = 'error'
        elif village not in [v.lower() for v in villages]:
            message = "Please select a valid village"
            message_type = 'error'
        elif task not in task_labels:
            message = "Please select a valid task type"
            message_type = 'error'
        else:
            try:
                wage = float(wage) if wage else 0.0
                num_workers = int(num_workers) if num_workers else 1
               
                if wage < 0:
                    message = "Wage cannot be negative"
                    message_type = 'error'
                elif num_workers < 1 or num_workers > 50:
                    message = "Number of workers must be between 1 and 50"
                    message_type = 'error'
                else:
                    location = f"{village}, {mandal}"
                    coordinates = get_coordinates(location, village)
                   
                    if not coordinates:
                        message = "Could not find coordinates for the specified village."
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
                            message = f"A similar {task} task already exists for {location.capitalize()}"
                            message_type = 'warning'
                        else:
                            result = tasks_collection.insert_one(task_data)
                            if result.inserted_id:
                                message = f"✅ Task '{task}' successfully posted for {village.capitalize()}, {mandal.capitalize()} with wage ₹{wage}/day for {num_workers} worker(s)"
                                message_type = 'success'
                                logger.info(f"Task {task} posted successfully for {location} by farmer {session['farmer_id']}")
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
    return render_template('farmer.html', message=message, message_type=message_type, tasks=task_labels, villages=villages, farmer=farmer, accepted_overview=accepted_overview, notifications=notifications, my_open_tasks=my_open_tasks)

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

@app.route('/volunteer_login', methods=['GET', 'POST'])
def volunteer_login():
    if request.method == 'POST':
        try:
            name = request.form.get('name', '').strip()
            password = request.form.get('password', '').strip()
           
            logger.info(f"Volunteer login attempt for: {name}")
           
            if not name or not password:
                return render_template('volunteer_login.html', error="Please enter both name and password")
           
            volunteer = volunteers_collection.find_one({"name": name})
           
            if not volunteer:
                logger.warning(f"Volunteer not found: {name}")
                return render_template('volunteer_login.html', error="Volunteer not found. Please check your name or register first.")
           
            if not check_password_hash(volunteer['password'], password):
                logger.warning(f"Invalid password for volunteer: {name}")
                return render_template('volunteer_login.html', error="Invalid password. Please try again.")
           
            session['volunteer_id'] = str(volunteer['_id'])
            logger.info(f"Volunteer {name} logged in successfully, redirecting to dashboard")
            flash("Volunteer login successful! Welcome to the dashboard.", "success")
            return redirect('/volunteer')
        except Exception as e:
            logger.error(f"Error in volunteer login: {e}")
            return render_template('volunteer_login.html', error=f"Login error: {str(e)}")
   
    logger.info("Rendering volunteer login page")
    return render_template('volunteer_login.html')

@app.route('/volunteer_reg', methods=['GET', 'POST'])
def volunteer_reg():
    if request.method == 'POST':
        name = request.form.get('name', '').strip()
        password = request.form.get('password', '').strip()
        phone = request.form.get('phone', '').strip()
        village = request.form.get('village', '').strip().lower()
        if not all([name, password, phone, village]):
            return render_template('volunteer_reg.html', error="All fields required", villages=villages)
        if volunteers_collection.find_one({"name": name}):
            return render_template('volunteer_reg.html', error="Name already exists", villages=villages)
        if village not in [v.lower() for v in villages]:
            return render_template('volunteer_reg.html', error="Invalid village", villages=villages)
        try:
            volunteer_data = {
                "name": name,
                "password": generate_password_hash(password),
                "phone": phone,
                "village": village
            }
            result = volunteers_collection.insert_one(volunteer_data)
            session['volunteer_id'] = str(result.inserted_id)
            logger.info(f"Volunteer {name} registered successfully, redirecting to home")
            flash("Volunteer registration completed successfully!", "success")
            return redirect('/')
        except Exception as e:
            logger.error(f"Error registering volunteer {name}: {e}")
            return render_template('volunteer_reg.html', error=f"Registration failed: {str(e)}", villages=villages)
   
    logger.info("Rendering volunteer registration page")
    return render_template('volunteer_reg.html', villages=villages)

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
   
    message = None
    message_type = 'info'
   
    if request.method == 'POST':
        # Handle request actions (accept/decline)
        action = request.form.get('action')
        if action in ['accept_request', 'decline_request']:
            help_request_id = request.form.get('request_id')
            if help_request_id:
                try:
                    new_status = 'accepted' if action == 'accept_request' else 'declined'
                    requests_collection.update_one(
                        {"_id": ObjectId(help_request_id), "volunteer_id": session['volunteer_id']},
                        {"$set": {"status": new_status, "responded_at": time.time()}}
                    )
                    
                    if action == 'accept_request':
                        # Get farmer details to create a task for workers
                        help_request = requests_collection.find_one({"_id": ObjectId(help_request_id)})
                        if help_request:
                            farmer = farmers_collection.find_one({"_id": ObjectId(help_request['farmer_id'])})
                            if farmer:
                                # Create a task for workers based on farmer's location
                                farmer_village = farmer.get('village', 'gudlavalleru').lower()
                                if farmer_village in [v.lower() for v in villages]:
                                    # Get coordinates for the farmer's village
                                    location = f"{farmer_village}, gudlavalleru"
                                    coordinates = get_coordinates(location, farmer_village)
                                    
                                    if coordinates:
                                        # Create a general help task for workers
                                        task_data = {
                                            "location": location,
                                            "task": "General Farm Help",
                                            "village": farmer_village.capitalize(),
                                            "mandal": "Gudlavalleru",
                                            "coordinates": {"type": "Point", "coordinates": coordinates},
                                            "wage": 500.0,  # Default wage for help tasks
                                            "num_workers": 2,  # Default number of workers
                                            "description": f"Help request from farmer {farmer.get('name', 'Unknown')} - Contact volunteer for details",
                                            "posted_by": session['volunteer_id'],
                                            "farmer_id": help_request['farmer_id'],
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
                                        
                                        # Check if similar task already exists
                                        existing_task = tasks_collection.find_one({
                                            "help_request_id": str(help_request_id),
                                            "status": "open"
                                        })
                                        
                                        if not existing_task:
                                            result = tasks_collection.insert_one(task_data)
                                            if result.inserted_id:
                                                message = f"Request accepted! A help task has been created for workers in {farmer_village.capitalize()}. You can now contact the farmer to provide assistance."
                                                message_type = 'success'
                                                logger.info(f"Help task created for farmer {farmer.get('name')} in {farmer_village}")
                                            else:
                                                message = "Request accepted but failed to create worker task. Please try again."
                                                message_type = 'warning'
                                        else:
                                            message = f"Request accepted! A help task already exists for this farmer in {farmer_village.capitalize()}."
                                            message_type = 'success'
                                    else:
                                        message = "Request accepted but could not create worker task due to location issues."
                                        message_type = 'warning'
                                else:
                                    message = "Request accepted but farmer's village is not valid for task creation."
                                    message_type = 'warning'
                            else:
                                message = "Request accepted but farmer details not found."
                                message_type = 'warning'
                        else:
                            message = "Request accepted but help request not found."
                            message_type = 'warning'
                    else:
                        message = f"Request declined."
                        message_type = 'info'
                except Exception as e:
                    logger.error(f"Error processing request action {action}: {e}")
                    message = f"Error processing request: {str(e)}"
                    message_type = 'error'
            else:
                message = "Invalid request ID"
                message_type = 'error'
        else:
            # Handle task posting
            village = request.form.get('village', '').strip().lower()
            task = request.form.get('task', '').strip()
            wage = request.form.get('wage', '').strip()
            num_workers = request.form.get('num_workers', '').strip()
            description = request.form.get('description', '').strip()
           
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
                    wage = float(wage) if wage else 0.0
                    num_workers = int(num_workers) if num_workers else 1
                   
                    if wage < 0:
                        message = "Wage cannot be negative"
                        message_type = 'error'
                    elif num_workers < 1 or num_workers > 50:
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
                                "wage": wage,
                                "num_workers": num_workers,
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
                                    message = f"✅ Task '{task}' successfully posted for {location.capitalize()} with wage ₹{wage}/day for {num_workers} worker(s)"
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
        farmer = farmers_collection.find_one({"_id": ObjectId(help_request['farmer_id'])})
        if farmer:
            help_request['farmer_name'] = farmer.get('name', 'Unknown Farmer')
            # Add farmer details for display
            help_request['farmer_details'] = {
                'village': farmer.get('village', ''),
                'phone': farmer.get('phone', ''),
                'location': farmer.get('location', '')
            }
        else:
            help_request['farmer_name'] = 'Unknown Farmer'
            help_request['farmer_details'] = None
        pending_requests.append(help_request)
   
    logger.info(f"Rendering volunteer dashboard for {volunteer['name']} with {len(pending_requests)} pending requests")
    return render_template('volunteer.html', message=message, message_type=message_type, tasks=task_labels, villages=villages, volunteer=volunteer, pending_requests=pending_requests)

@app.route('/worker_login', methods=['GET', 'POST'])
def worker_login():
    if request.method == 'POST':
        try:
            name = request.form.get('name', '').strip()
            password = request.form.get('password', '').strip()
           
            logger.info(f"Worker login attempt for: {name}")
           
            if not name or not password:
                return render_template('worker_login.html', error="Please enter both name and password")
           
            worker = workers_collection.find_one({"name": name})
           
            if not worker:
                logger.warning(f"Worker not found: {name}")
                return render_template('worker_login.html', error="Worker not found. Please check your name or register first.")
           
            if not check_password_hash(worker['password'], password):
                logger.warning(f"Invalid password for worker: {name}")
                return render_template('worker_login.html', error="Invalid password. Please try again.")
           
            session['worker_id'] = str(worker['_id'])
            logger.info(f"Worker {name} logged in successfully, redirecting to dashboard")
            flash("Worker login successful! Welcome to the dashboard.", "success")
            return redirect('/worker')
        except Exception as e:
            logger.error(f"Error in worker login: {e}")
            return render_template('worker_login.html', error=f"Login error: {str(e)}")
   
    logger.info("Rendering worker login page")
    return render_template('worker_login.html')

@app.route('/worker_reg', methods=['GET', 'POST'])
def worker_reg():
    if request.method == 'POST':
        name = request.form.get('name', '').strip()
        password = request.form.get('password', '').strip()
        phone = request.form.get('phone', '').strip()
        village = request.form.get('village', '').strip().lower()
        location = f"{village}, gudlavalleru"
        if not all([name, password, phone, village]):
            return render_template('worker_reg.html', error="All fields required", villages=villages)
        if workers_collection.find_one({"name": name}):
            return render_template('worker_reg.html', error="Name already exists", villages=villages)
        if village not in [v.lower() for v in villages]:
            return render_template('worker_reg.html', error="Invalid village", villages=villages)
        coordinates = get_coordinates(location, village)
        if not coordinates:
            return render_template('worker_reg.html', error="Could not find location", villages=villages)
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
            logger.info(f"Worker {name} registered successfully, redirecting to home")
            flash("Worker registration completed successfully!", "success")
            return redirect('/')
        except Exception as e:
            logger.error(f"Error registering worker {name}: {e}")
            return render_template('worker_reg.html', error=f"Registration failed: {str(e)}", villages=villages)
   
    logger.info("Rendering worker registration page")
    return render_template('worker_reg.html', villages=villages)

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
   
    nearby_tasks = []
    message = None
    message_type = 'info'
   
    should_fetch_on_get = request.method == 'GET' and request.args.get('fetch') == '1'
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
   
    logger.info(f"Rendering worker dashboard for {worker['name']} with {len(accepted_tasks)} accepted tasks")
    return render_template('worker.html', nearby_tasks=nearby_tasks, accepted_tasks=accepted_tasks, worker=worker, message=message, message_type=message_type)

@app.route('/logout')
def logout():
    logger.info("User logging out, clearing session")
    session.clear()
    flash("You have been logged out successfully.", "success")
    return redirect('/')

@app.route('/admin', methods=['GET', 'POST'])
def admin():
    message = None
    if request.method == 'POST':
        village = request.form.get('village', '').strip().lower()
        task = request.form.get('task', '').strip()
        if not all([village, task]):
            message = "Please select both village and task"
        elif village not in [v.lower() for v in villages]:
            message = "Please select a valid village"
        else:
            # Add logic to assign task (e.g., update tasks_collection)
            message = f"Task '{task}' assigned to {village.capitalize()}"
    return render_template('admin.html', villages=villages, tasks=task_labels, message=message)

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
    app.run(debug=True, port=5001)