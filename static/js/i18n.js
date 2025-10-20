// Simple client-side i18n for EN, TE, HI
// Usage:
// - Add data-i18n="key" to set element textContent
// - Optional: data-i18n-placeholder / data-i18n-value to translate attributes

const TRANSLATIONS = {
  en: {
    "nav.language": "Language",
    "nav.volunteer": "Volunteer",
    "nav.farmer": "Farmer",
    "nav.logout": "Logout",

    "hero.title": "Welcome",
    "hero.subtitle": "Empowering Agriculture Through Smart Rural Labor Hiring",

    "card.workers.title": "Workers",
    "card.workers.desc": "Find and accept nearby farming jobs.",
    "btn.register_worker": "Register as Worker",
    "btn.login_worker": "Login as Worker",

    // Worker auth
    "login.worker.title": "Worker Login",
    "login.worker.name": "Name",
    "login.worker.password": "Password",
    "login.worker.button": "Login",
    "login.worker.signup": "New worker?",
    
    "reg.worker.title": "Worker Registration",
    "reg.worker.name": "Name",
    "reg.worker.password": "Password",
    "reg.worker.village": "Village",
    "reg.worker.button": "Register",
    "reg.worker.loginlink": "Already registered?",

    "card.volunteers.title": "Volunteers",
    "card.volunteers.desc": "Post agricultural tasks for your community.",
    "card.volunteers.cta": "Login as Volunteer",

    "card.farmers.title": "Farmers",
    "card.farmers.desc": "Posting the job.",
    "card.farmers.cta": "Login as Farmer",

    "btn.register_farmer": "Register as Farmer",
    "btn.login_farmer": "Login as Farmer",
    "btn.register_volunteer": "Register as Volunteer",
    "btn.login_volunteer": "Login as Volunteer",

    "card.newfarmer.title": "New Farmer?",
    "card.newfarmer.desc": "Register to start working on local tasks.",
    "card.newfarmer.cta": "Register Now",

    "login.volunteer.title": "Volunteer Login",
    "login.volunteer.name": "Name",
    "login.volunteer.password": "Password",
    "login.volunteer.button": "Login",
    "login.volunteer.signup": "New volunteer?",

    "login.farmer.title": "Farmer Login",
    "login.farmer.name": "Name",
    "login.farmer.password": "Password",
    "login.farmer.button": "Login",
    "login.farmer.signup": "New farmer?",

    "reg.volunteer.title": "Volunteer Registration",
    "reg.volunteer.name": "Name",
    "reg.volunteer.password": "Password",
    "reg.volunteer.button": "Register",
    "reg.volunteer.loginlink": "Already registered?",

    "reg.farmer.title": "Farmer Registration",
    "reg.farmer.name": "Farmer Name",
    "reg.farmer.password": "Password",
    "reg.farmer.district": "District",
    "reg.farmer.village": "Village",
    "reg.farmer.button": "Register",
    "reg.farmer.loginlink": "Already registered?",

    "vol.dashboard.title": "Post a New Task",
    "vol.form.district": "District",
    "vol.form.village": "Village",
    "vol.form.task": "Task",
    "vol.form.wage": "Wage per day (₹)",
    "vol.form.num_workers": "Number of Workers Required",
    "vol.form.description": "Description (Optional)",
    "vol.form.submit": "Post Task",

    "farmer.dashboard.welcome": "Welcome",
    "farmer.available.title": "Available Nearby Tasks",
    "farmer.accepted.title": "Accepted Tasks",
    "farmer.fetch": "Fetch Nearby Tasks",
    "action.accept": "Accept",
    "action.deny": "Deny",

    // Generic labels used in dashboards
    "label.task": "Task",
    "label.location": "Location",
    "label.wage": "Wage",
    "label.workers_needed": "Workers Needed",
    "label.distance": "Distance",
    "label.description": "Description",
    "label.phone": "Phone Number",
    "label.status": "Status",
    "label.accepted_by": "Accepted By",
    "label.required": "Required",
    "label.village": "Village",
    "label.mandal": "Mandal",

    // Worker dashboard
    "worker.dashboard.title": "Worker Dashboard",
    "worker.fetch": "Fetch Nearby Tasks",
    "worker.nearby.title": "Nearby Tasks",
    "worker.accepted.title": "Accepted Tasks",
    "action.accept_task": "Accept Task",
    "action.deny_task": "Deny Task",
    "action.complete": "Mark as Complete",

    // Additional labels for forms and UI
    "form.select_village": "Select Village",
    "form.select_task": "Select Task",
    "form.select_mandal": "Select Mandal",
    "form.enter_name": "Enter your name",
    "form.enter_password": "Enter your password",
    "form.create_password": "Create a password",
    "form.enter_phone": "Enter your phone number",
    "form.enter_worker_phone": "Enter worker phone",
    "form.enter_description": "Enter description",
    "form.enter_wage": "Enter wage per day",

    // Status messages
    "status.completed": "Completed",
    "status.in_progress": "In Progress",
    "status.pending": "Pending",
    "status.open": "Open",
    "status.filled": "Filled",

    // Dashboard sections
    "dashboard.assign_task": "Assign Task to a Worker",
    "dashboard.select_task": "Select Your Task",
    "dashboard.notifications": "Notifications",
    "dashboard.check_notifications": "Check Notifications",
    "dashboard.help_request_status": "Help Request Status",
    "dashboard.check_help_status": "Check Help Request Status",
    "dashboard.pending_requests": "Pending Help Requests",
    "dashboard.accepted_workers": "Accepted Workers",
    "dashboard.recent_notifications": "Recent Notifications",
    "dashboard.contact_info": "Contact Information",

    // Messages and alerts
    "message.no_workers_accepted": "No workers accepted yet. Your task is still open for workers to accept.",
    "message.help_request_accepted": "Your help request is accepted by workers:",
    "message.contact_workers": "You can now contact these workers directly or through the volunteer for assistance.",
    "message.assigned_tasks_info": "Assigned tasks will only be visible to that worker in their dashboard.",
    "message.no_description": "No description",
    "message.task_assigned": "Task assigned to",
    "message.worker_not_found": "Worker with this phone not found.",
    "message.task_not_found": "Task not found or not owned by you.",
    "message.task_already_filled": "Task already has required number of workers.",

    // Navigation and common actions
    "nav.back_to_home": "Back to Home",
    "nav.register_here": "Register here",
    "nav.login_here": "Login here",
    "nav.need_help": "Need Help from Volunteer",
    "nav.request_help": "Request Help from Volunteers",

    // Admin specific translations
    "admin.title": "Admin Dashboard",
    "admin.welcome": "Welcome Admin",
    "admin.assign_task": "Assign Task to Location",
    "admin.select_village": "Select Village",
    "admin.select_task": "Select Task",
    "admin.assign": "Assign",

    // Help request specific translations
    "help.request_title": "Help Request",
    "help.volunteers_by_village": "Volunteers by Village",
    "help.request_help": "Request Help",
    "help.contact_volunteer": "Contact Volunteer",
    "help.no_volunteer": "No volunteer in this village",

    // Phone and contact related
    "contact.phone": "Phone Number",
    "contact.village": "Village",
    "contact.location": "Location",
    "contact.worker_phone": "Worker Phone Number",
    "contact.enter_worker_phone": "Enter worker phone number",

    // Task related translations
    "task.sowing": "Sowing",
    "task.weeding": "Weeding",
    "task.harvesting": "Harvesting",
    "task.irrigation": "Irrigation",
    "task.general_help": "General Farm Help",

    // Village names in English (keeping original names)
    "village.angaluru": "Angaluru",
    "village.chandrala": "Chandrala",
    "village.chinagonnuru": "Chinagonnuru",
    "village.chitram": "Chitram",
    "village.dokiparru": "Dokiparru",
    "village.gadepudi": "Gadepudi",
    "village.gudlavalleru": "Gudlavalleru",
    "village.kowtaram": "Kowtaram",
    "village.kurada": "Kurada",
    "village.mamidikolla": "Mamidikolla",
    "village.nagavaram": "Nagavaram",
    "village.penjendra": "Penjendra",
    "village.pesaramilli": "Pesaramilli",
    "village.puritipadu": "Puritipadu",
    "village.seri_kalavapudi": "Seri Kalavapudi",
    "village.seridaggumilli": "Seridaggumilli",
    "village.ulavalapudi": "Ulavalapudi",
    "village.vadlamannadu": "Vadlamannadu",
    "village.vemavaram": "Vemavaram",
    "village.vemavarappalem": "Vemavarappalem",
    "village.venuturumilli": "Venuturumilli",
    "village.vinnakota": "Vinnakota",

    // Additional form labels
    "form.phone_number": "Phone Number",
    "form.enter_phone_number": "Enter your phone number",
    "form.select_village_placeholder": "Select Village",
    "form.select_task_placeholder": "Select Task",
    "form.select_mandal_placeholder": "Select Mandal",

    // Status and messages
    "status.accepted": "Accepted",
    "status.declined": "Declined",
    "status.pending": "Pending",
    "status.filled": "Filled",
    "status.assigned": "Assigned",

    // Time related
    "time.created_at": "Created At",
    "time.responded_at": "Responded At",
    "time.completed_at": "Completed At",

    // Farmer specific dashboard
    "farmer.post_task": "Post a New Task",
    "farmer.assign_task_to_worker": "Assign Task to a Worker",
    "farmer.select_your_task": "Select Your Task",
    "farmer.worker_phone_number": "Worker Phone Number",
    "farmer.assign_task_button": "Assign Task",
    "farmer.notifications": "Notifications",
    "farmer.check_notifications": "Check Notifications",
    "farmer.help_request_status": "Help Request Status",
    "farmer.check_help_status": "Check Help Request Status",
    "farmer.recent_notifications": "Recent Notifications",
    "farmer.help_request_accepted": "Your help request is accepted by workers",
    "farmer.contact_workers": "You can now contact these workers directly or through the volunteer for assistance",
    "farmer.assigned_tasks_info": "Assigned tasks will only be visible to that worker in their dashboard",
    "farmer.no_workers_accepted_yet": "No workers accepted yet. Your help request is still open for workers to accept",

    // Worker specific dashboard
    "worker.posted_by": "Posted By",
    "worker.accepted_workers": "Accepted Workers",
    "worker.required_workers": "Required Workers",
    "worker.assigned_to": "Assigned To",
    "worker.task_type": "Task Type",

    // Volunteer specific dashboard
    "volunteer.pending_help_requests": "Pending Help Requests",
    "volunteer.request_from_farmer": "Request from Farmer",
    "volunteer.farmer_details": "Farmer Details",
    "volunteer.not_specified": "Not specified",
    "volunteer.not_provided": "Not provided",
    "volunteer.accept": "Accept",
    "volunteer.decline": "Decline"
  },
  te: {
    "nav.language": "భాష",
    "nav.volunteer": "సేవకుడు",
    "nav.farmer": "రైతు",
    "nav.logout": "లాగ్ అవుట్",

    "hero.title": "రైతు నేస్తం",
    "hero.subtitle": "స్మార్ట్ కార్మిక నియామకంతో గ్రామీణ రైతులకు శక్తినిస్తాం",

    "card.workers.title": "కార్మికులు",
    "card.workers.desc": "దగ్గరలోని వ్యవసాయ పనులను కనుగొని అంగీకరించండి.",
    "btn.register_worker": "కార్మికుడిగా నమోదు",
    "btn.login_worker": "కార్మికుడిగా లాగిన్",

    // Worker auth
    "login.worker.title": "కార్మికుడు లాగిన్",
    "login.worker.name": "పేరు",
    "login.worker.password": "పాస్‌వర్డ్",
    "login.worker.button": "లాగిన్",
    "login.worker.signup": "కొత్త కార్మికుడా?",

    "reg.worker.title": "కార్మికుడు నమోదు",
    "reg.worker.name": "పేరు",
    "reg.worker.password": "పాస్‌వర్డ్",
    "reg.worker.village": "గ్రామం",
    "reg.worker.button": "నమోదు",
    "reg.worker.loginlink": "ఇప్పటికే నమోదు చేసుకున్నారా?",

    "card.volunteers.title": "సేవకులు",
    "card.volunteers.desc": "మీ సమాజం కోసం వ్యవసాయ పనులను పోస్ట్ చేయండి.",
    "card.volunteers.cta": "సేవకుడిగా లాగిన్ అవ్వండి",

    "card.farmers.title": "రైతులు",
    "card.farmers.desc": "దగ్గరలోని వ్యవసాయ పనులను కనుగొని అంగీకరించండి.",
    "card.farmers.cta": "రైతుగా లాగిన్ అవ్వండి",

    "btn.register_farmer": "రైతుగా నమోదు",
    "btn.login_farmer": "రైతుగా లాగిన్",
    "btn.register_volunteer": "సేవకుడిగా నమోదు",
    "btn.login_volunteer": "సేవకుడిగా లాగిన్",

    "card.newfarmer.title": "కొత్త రైతువారా?",
    "card.newfarmer.desc": "స్థానిక పనులకు నమోదు చేసుకోండి.",
    "card.newfarmer.cta": "ఇప్పుడు నమోదు చేసుకోండి",

    "login.volunteer.title": "సేవకుడు లాగిన్",
    "login.volunteer.name": "పేరు",
    "login.volunteer.password": "పాస్‌వర్డ్",
    "login.volunteer.button": "లాగిన్",
    "login.volunteer.signup": "కొత్త సేవకుడా?",

    "login.farmer.title": "రైతు లాగిన్",
    "login.farmer.name": "పేరు",
    "login.farmer.password": "పాస్‌వర్డ్",
    "login.farmer.button": "లాగిన్",
    "login.farmer.signup": "కొత్త రైతువారా?",

    "reg.volunteer.title": "సేవకుడు నమోదు",
    "reg.volunteer.name": "పేరు",
    "reg.volunteer.password": "పాస్‌వర్డ్",
    "reg.volunteer.button": "నమోదు",
    "reg.volunteer.loginlink": "ఇప్పటికే నమోదు చేసుకున్నారా?",

    "reg.farmer.title": "రైతు నమోదు",
    "reg.farmer.name": "రైతు పేరు",
    "reg.farmer.password": "పాస్‌వర్డ్",
    "reg.farmer.district": "జిల్లా",
    "reg.farmer.village": "గ్రామం",
    "reg.farmer.button": "నమోదు",
    "reg.farmer.loginlink": "ఇప్పటికే నమోదు చేసుకున్నారా?",

    "vol.dashboard.title": "కొత్త పనిని పోస్ట్ చేయండి",
    "vol.form.district": "జిల్లా",
    "vol.form.village": "గ్రామం",
    "vol.form.task": "పని",
    "vol.form.wage": "రోజుకి వేతనం (₹)",
    "vol.form.num_workers": "కావలసిన కార్మికుల సంఖ్య",
    "vol.form.description": "వివరణ (ఐచ్చికం)",
    "vol.form.submit": "పని పోస్ట్ చేయండి",

    "farmer.dashboard.welcome": "స్వాగతం",
    "farmer.available.title": "దగ్గరలో లభ్యమయ్యే పనులు",
    "farmer.accepted.title": "అంగీకరించిన పనులు",
    "farmer.fetch": "దగ్గరలోని పనులను తెచ్చుకోండి",
    "action.accept": "అంగీకరించు",
    "action.deny": "తిరస్కరించు",

    // Generic labels used in dashboards
    "label.task": "పని",
    "label.location": "స్థానం",
    "label.wage": "వేతనం",
    "label.workers_needed": "కావలసిన కార్మికులు",
    "label.distance": "దూరం",
    "label.description": "వివరణ",
    "label.phone": "ఫోన్ నంబర్",
    "label.status": "స్థితి",
    "label.accepted_by": "అంగీకరించినవారు",
    "label.required": "కావలసిన",
    "label.village": "గ్రామం",
    "label.mandal": "మండలం",

    // Worker dashboard
    "worker.dashboard.title": "కార్మికుల డ్యాష్‌బోర్డ్",
    "worker.fetch": "దగ్గరలోని పనులను తెచ్చుకోండి",
    "worker.nearby.title": "దగ్గరలోని పనులు",
    "worker.accepted.title": "అంగీకరించిన పనులు",
    "action.accept_task": "పనిని అంగీకరించు",
    "action.deny_task": "పనిని తిరస్కరించు",
    "action.complete": "పూర్తి అయినట్లు గుర్తించు",

    // Additional labels for forms and UI
    "form.select_village": "గ్రామాన్ని ఎంచుకోండి",
    "form.select_task": "పనిని ఎంచుకోండి",
    "form.select_mandal": "మండలాన్ని ఎంచుకోండి",
    "form.enter_name": "మీ పేరును నమోదు చేయండి",
    "form.enter_password": "మీ పాస్‌వర్డ్‌ను నమోదు చేయండి",
    "form.create_password": "పాస్‌వర్డ్‌ను సృష్టించండి",
    "form.enter_phone": "మీ ఫోన్ నంబర్‌ను నమోదు చేయండి",
    "form.enter_worker_phone": "కార్మికుడి ఫోన్ నంబర్‌ను నమోదు చేయండి",
    "form.enter_description": "వివరణను నమోదు చేయండి",
    "form.enter_wage": "రోజుకి వేతనాన్ని నమోదు చేయండి",

    // Status messages
    "status.completed": "పూర్తయింది",
    "status.in_progress": "ప్రగతిలో ఉంది",
    "status.pending": "వేచి ఉంది",
    "status.open": "తెరిచిన",
    "status.filled": "నిండింది",

    // Dashboard sections
    "dashboard.assign_task": "కార్మికుడికి పనిని కేటాయించండి",
    "dashboard.select_task": "మీ పనిని ఎంచుకోండి",
    "dashboard.notifications": "నోటిఫికేషన్‌లు",
    "dashboard.check_notifications": "నోటిఫికేషన్‌లను తనిఖీ చేయండి",
    "dashboard.help_request_status": "సహాయ అభ్యర్థన స్థితి",
    "dashboard.check_help_status": "సహాయ అభ్యర్థన స్థితిని తనిఖీ చేయండి",
    "dashboard.pending_requests": "వేచిఉన్న సహాయ అభ్యర్థనలు",
    "dashboard.accepted_workers": "అంగీకరించిన కార్మికులు",
    "dashboard.recent_notifications": "ఇటీవలి నోటిఫికేషన్‌లు",
    "dashboard.contact_info": "సంప్రదింపు సమాచారం",

    // Messages and alerts
    "message.no_workers_accepted": "ఇంకా ఎవరూ కార్మికులు అంగీకరించలేదు. మీ పని ఇంకా కార్మికులు అంగీకరించడానికి తెరిచి ఉంది.",
    "message.help_request_accepted": "మీ సహాయ అభ్యర్థన కార్మికులచే అంగీకరించబడింది:",
    "message.contact_workers": "మీరు ఇప్పుడు ఈ కార్మికులను నేరుగా లేదా సేవకుడి ద్వారా సహాయం కోసం సంప్రదించవచ్చు.",
    "message.assigned_tasks_info": "కేటాయించిన పనులు ఆ కార్మికుడి డ్యాష్‌బోర్డ్‌లో మాత్రమే కనిపిస్తాయి.",
    "message.no_description": "వివరణ లేదు",
    "message.task_assigned": "పని కేటాయించబడింది",
    "message.worker_not_found": "ఈ ఫోన్‌తో కార్మికుడు కనుగొనబడలేదు.",
    "message.task_not_found": "పని కనుగొనబడలేదు లేదా మీది కాదు.",
    "message.task_already_filled": "పనికి ఇప్పటికే కావలసిన కార్మికుల సంఖ్య ఉంది.",

    // Navigation and common actions
    "nav.back_to_home": "హోమ్‌కు తిరిగి వెళ్ళండి",
    "nav.register_here": "ఇక్కడ నమోదు చేయండి",
    "nav.login_here": "ఇక్కడ లాగిన్ అవ్వండి",
    "nav.need_help": "సేవకుడి నుండి సహాయం కావాలి",
    "nav.request_help": "సేవకుల నుండి సహాయం అభ్యర్థించండి",

    // Admin specific translations
    "admin.title": "అడ్మిన్ డ్యాష్‌బోర్డ్",
    "admin.welcome": "స్వాగతం అడ్మిన్",
    "admin.assign_task": "స్థానానికి పనిని కేటాయించండి",
    "admin.select_village": "గ్రామాన్ని ఎంచుకోండి",
    "admin.select_task": "పనిని ఎంచుకోండి",
    "admin.assign": "కేటాయించండి",

    // Help request specific translations
    "help.request_title": "సహాయం అభ్యర్థన",
    "help.volunteers_by_village": "గ్రామం వారీగా సేవకులు",
    "help.request_help": "సహాయం అభ్యర్థించండి",
    "help.contact_volunteer": "సేవకుడిని సంప్రదించండి",
    "help.no_volunteer": "ఈ గ్రామంలో సేవకుడు లేరు",

    // Phone and contact related
    "contact.phone": "ఫోన్ నంబర్",
    "contact.village": "గ్రామం",
    "contact.location": "స్థానం",
    "contact.worker_phone": "కార్మికుడి ఫోన్ నంబర్",
    "contact.enter_worker_phone": "కార్మికుడి ఫోన్ నంబర్‌ను నమోదు చేయండి",

    // Task related translations
    "task.sowing": "విత్తనం",
    "task.weeding": "కలుపు తొలగింపు",
    "task.harvesting": "పంట కోత",
    "task.irrigation": "నీటి పారుదల",
    "task.general_help": "సాధారణ వ్యవసాయ సహాయం",

    // Village names in Telugu
    "village.angaluru": "అంగలూరు",
    "village.chandrala": "చంద్రల",
    "village.chinagonnuru": "చినగొన్నూరు",
    "village.chitram": "చిత్రం",
    "village.dokiparru": "డోకిపర్రు",
    "village.gadepudi": "గడేపూడి",
    "village.gudlavalleru": "గూడ్లవల్లేరు",
    "village.kowtaram": "కోవ్తరం",
    "village.kurada": "కురాడ",
    "village.mamidikolla": "మామిడికొల్ల",
    "village.nagavaram": "నాగవరం",
    "village.penjendra": "పెంజెండ్ర",
    "village.pesaramilli": "పేసరమిల్లి",
    "village.puritipadu": "పురితిపాడు",
    "village.seri_kalavapudi": "సేరి కలవపూడి",
    "village.seridaggumilli": "సేరిడగ్గుమిల్లి",
    "village.ulavalapudi": "ఉలవలపూడి",
    "village.vadlamannadu": "వడ్లమన్నాడు",
    "village.vemavaram": "వేమవరం",
    "village.vemavarappalem": "వేమవరప్పాలెం",
    "village.venuturumilli": "వేణుతురుమిల్లి",
    "village.vinnakota": "విన్నకోట",

    // Additional form labels
    "form.phone_number": "ఫోన్ నంబర్",
    "form.enter_phone_number": "ఫోన్ నంబర్‌ను నమోదు చేయండి",
    "form.select_village_placeholder": "గ్రామాన్ని ఎంచుకోండి",
    "form.select_task_placeholder": "పనిని ఎంచుకోండి",
    "form.select_mandal_placeholder": "మండలాన్ని ఎంచుకోండి",

    // Status and messages
    "status.accepted": "అంగీకరించబడింది",
    "status.declined": "తిరస్కరించబడింది",
    "status.pending": "వేచి ఉంది",
    "status.filled": "నిండింది",
    "status.assigned": "కేటాయించబడింది",

    // Time related
    "time.created_at": "సృష్టించిన సమయం",
    "time.responded_at": "సమాధానం ఇచ్చిన సమయం",
    "time.completed_at": "పూర్తి చేసిన సమయం",

    // Farmer specific dashboard
    "farmer.post_task": "కొత్త పనిని పోస్ట్ చేయండి",
    "farmer.assign_task_to_worker": "కార్మికుడికి పనిని కేటాయించండి",
    "farmer.select_your_task": "మీ పనిని ఎంచుకోండి",
    "farmer.worker_phone_number": "కార్మికుడి ఫోన్ నంబర్",
    "farmer.assign_task_button": "పనిని కేటాయించండి",
    "farmer.notifications": "నోటిఫికేషన్‌లు",
    "farmer.check_notifications": "నోటిఫికేషన్‌లను తనిఖీ చేయండి",
    "farmer.help_request_status": "సహాయ అభ్యర్థన స్థితి",
    "farmer.check_help_status": "సహాయ అభ్యర్థన స్థితిని తనిఖీ చేయండి",
    "farmer.recent_notifications": "ఇటీవలి నోటిఫికేషన్‌లు",
    "farmer.help_request_accepted": "మీ సహాయ అభ్యర్థన కార్మికులచే అంగీకరించబడింది",
    "farmer.contact_workers": "మీరు ఇప్పుడు ఈ కార్మికులను నేరుగా లేదా సేవకుడి ద్వారా సహాయం కోసం సంప్రదించవచ్చు",
    "farmer.assigned_tasks_info": "కేటాయించిన పనులు ఆ కార్మికుడి డ్యాష్‌బోర్డ్‌లో మాత్రమే కనిపిస్తాయి",
    "farmer.no_workers_accepted_yet": "ఇంకా ఎవరూ కార్మికులు అంగీకరించలేదు. మీ సహాయ అభ్యర్థన ఇంకా కార్మికులు అంగీకరించడానికి తెరిచి ఉంది",

    // Worker specific dashboard
    "worker.posted_by": "పోస్ట్ చేసినవారు",
    "worker.accepted_workers": "అంగీకరించిన కార్మికులు",
    "worker.required_workers": "కావలసిన కార్మికులు",
    "worker.assigned_to": "కేటాయించబడింది",
    "worker.task_type": "పని రకం",

    // Volunteer specific dashboard
    "volunteer.pending_help_requests": "వేచిఉన్న సహాయ అభ్యర్థనలు",
    "volunteer.request_from_farmer": "రైతు నుండి అభ్యర్థన",
    "volunteer.farmer_details": "రైతు వివరాలు",
    "volunteer.not_specified": "నిర్దేశించబడలేదు",
    "volunteer.not_provided": "ఇవ్వబడలేదు",
    "volunteer.accept": "అంగీకరించు",
    "volunteer.decline": "తిరస్కరించు"
  },
  hi: {
    "nav.language": "भाषा",
    "nav.volunteer": "स्वयंसेवक",
    "nav.farmer": "किसान",
    "nav.logout": "लॉगआउट",

    "hero.title": "रैतुनेस्टम",
    "hero.subtitle": "स्मार्ट श्रम भर्ती के साथ ग्रामीण किसानों को सशक्त बनाएं",

    "card.volunteers.title": "स्वयंसेवक",
    "card.volunteers.desc": "अपने समुदाय के लिए कृषि कार्य पोस्ट करें।",
    "card.volunteers.cta": "स्वयंसेवक के रूप में लॉगिन",

    "card.farmers.title": "किसान",
    "card.farmers.desc": "नज़दीकी कृषि कार्य खोजें और स्वीकार करें।",
    "card.farmers.cta": "किसान के रूप में लॉगिन",

    "card.newfarmer.title": "नए किसान?",
    "card.newfarmer.desc": "स्थानीय कार्य शुरू करने के लिए पंजीकरण करें।",
    "card.newfarmer.cta": "अभी पंजीकरण करें",

    "login.volunteer.title": "स्वयंसेवक लॉगिन",
    "login.volunteer.name": "नाम",
    "login.volunteer.password": "पासवर्ड",
    "login.volunteer.button": "लॉगिन",
    "login.volunteer.signup": "नए स्वयंसेवक?",

    "login.farmer.title": "किसान लॉगिन",
    "login.farmer.name": "नाम",
    "login.farmer.password": "पासवर्ड",
    "login.farmer.button": "लॉगिन",
    "login.farmer.signup": "नए किसान?",

    "reg.volunteer.title": "स्वयंसेवक पंजीकरण",
    "reg.volunteer.name": "नाम",
    "reg.volunteer.password": "पासवर्ड",
    "reg.volunteer.button": "पंजीकरण",
    "reg.volunteer.loginlink": "पहले से पंजीकृत?",

    "reg.farmer.title": "किसान पंजीकरण",
    "reg.farmer.name": "किसान का नाम",
    "reg.farmer.password": "पासवर्ड",
    "reg.farmer.district": "जिला",
    "reg.farmer.village": "गाँव",
    "reg.farmer.button": "पंजीकरण",
    "reg.farmer.loginlink": "पहले से पंजीकृत?",

    "vol.dashboard.title": "नया कार्य पोस्ट करें",
    "vol.form.district": "जिला",
    "vol.form.village": "गाँव",
    "vol.form.task": "कार्य",
    "vol.form.wage": "प्रतिदिन मजदूरी (₹)",
    "vol.form.num_workers": "आवश्यक श्रमिकों की संख्या",
    "vol.form.description": "विवरण (वैकल्पिक)",
    "vol.form.submit": "कार्य पोस्ट करें",

    "farmer.dashboard.welcome": "स्वागत है",
    "farmer.available.title": "नज़दीकी उपलब्ध कार्य",
    "farmer.accepted.title": "स्वीकार किए गए कार्य",
    "farmer.fetch": "नज़दीकी कार्य प्राप्त करें",
    "action.accept": "स्वीकार",
    "action.deny": "अस्वीकार"
  }
};

function applyTranslations(lang) {
  const dict = TRANSLATIONS[lang] || TRANSLATIONS.en;
  document.querySelectorAll('[data-i18n]').forEach(el => {
    const key = el.getAttribute('data-i18n');
    if (dict[key]) el.textContent = dict[key];
  });
  document.querySelectorAll('[data-i18n-placeholder]').forEach(el => {
    const key = el.getAttribute('data-i18n-placeholder');
    if (dict[key]) el.setAttribute('placeholder', dict[key]);
  });
  document.querySelectorAll('[data-i18n-value]').forEach(el => {
    const key = el.getAttribute('data-i18n-value');
    if (dict[key]) el.setAttribute('value', dict[key]);
  });
}

function setLanguage(lang) {
  localStorage.setItem('preferred_language', lang);
  applyTranslations(lang);
}

document.addEventListener('DOMContentLoaded', () => {
  const saved = localStorage.getItem('preferred_language') || 'en';
  applyTranslations(saved);
  // Wire global handlers if present
  window.switchLanguage = setLanguage;
});


