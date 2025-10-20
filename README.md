# RaithuNestham - Smart Rural Labor Hiring Platform

## Project Overview

RaithuNestham is a smart rural labor hiring web platform designed to bridge the gap between farmers and nearby daily wage agricultural laborers (kulilu). The system enables farmers to post job requirements while allowing workers within a 10-kilometer radius to view and accept jobs in real time.

## Key Features

### ✅ Implemented Features
- **Location-based Job Matching**: GPS-based filtering using Haversine formula for precise 10km radius
- **Wage Specification**: Farmers can specify daily wages for tasks
- **Worker Count**: Specify number of workers required for each task
- **Task Management**: Support for Sowing, Weeding, Harvesting, and Irrigation tasks
- **Real-time Task Updates**: Live task posting and acceptance
- **User Authentication**: Separate login systems for farmers and volunteers
- **District-wise Village Support**: Comprehensive coverage of Telangana districts
- **Voice Input Support**: Basic voice input for form fields
- **Multilingual Interface**: Language switcher (English, Telugu, Hindi)
- **Responsive Design**: Mobile-friendly Bootstrap interface

### 🔧 Technical Improvements Made

#### 1. Fixed Village Cache Issue
- **Problem**: All districts had identical village lists (Hyderabad villages)
- **Solution**: Implemented district-specific village mappings for all 33 Telangana districts
- **Impact**: Proper geographic filtering and realistic village distribution

#### 2. Implemented Haversine Formula
- **Problem**: Abstract mentioned Haversine formula but code used basic MongoDB geo queries
- **Solution**: Added precise distance calculation using Haversine formula
- **Impact**: Accurate 10km radius filtering with distance display

#### 3. Enhanced Task Model
- **Added Fields**: wage, num_workers, description, status
- **Validation**: Proper input validation for wage and worker count
- **Status Tracking**: open, assigned, completed states

#### 4. Improved Error Handling
- **Comprehensive Validation**: Input validation with user-friendly error messages
- **Message Types**: Success, error, warning, and info messages
- **Logging**: Detailed error logging for debugging

#### 5. Added Missing Features
- **Wage Specification**: Daily wage input with validation
- **Worker Count**: Number of workers required (1-50)
- **Voice Input**: Basic speech recognition for form fields
- **Multilingual Support**: Language switcher with localStorage persistence

## Technology Stack

- **Backend**: Python Flask
- **Database**: MongoDB with geospatial indexing
- **Frontend**: HTML5, Bootstrap 5, JavaScript
- **Geocoding**: Nominatim (OpenStreetMap)
- **ML**: BERT for task prediction
- **Maps**: OSMnx for geographic data

## Installation & Setup

1. **Clone the repository**
   ```bash
   git clone <repository-url>
   cd RaithuNestham
   ```

2. **Create virtual environment**
   ```bash
   python -m venv venv
   source venv/bin/activate  # On Windows: venv\Scripts\activate
   ```

3. **Install dependencies**
   ```bash
   pip install -r requirements.txt
   ```

4. **Configure MongoDB**
   - Update MongoDB connection string in `app.py`
   - Ensure MongoDB Atlas cluster is accessible

5. **Run the application**
   ```bash
   python app.py
   ```

6. **Access the application**
   - Open browser to `http://localhost:5001`

## Project Structure

```
RaithuNestham/
├── app.py                 # Main Flask application
├── requirements.txt       # Python dependencies
├── villages.json         # Cached village data
├── templates/            # HTML templates
│   ├── index.html        # Home page
│   ├── farmer.html       # Farmer dashboard
│   ├── volunteer.html    # Volunteer dashboard
│   └── ...               # Other templates
└── cache/               # Application cache
```

## Key Routes

- `/` - Home page
- `/farmer_login` - Farmer login
- `/farmer_reg` - Farmer registration
- `/farmer` - Farmer dashboard
- `/volunteer_login` - Volunteer login
- `/volunteer_reg` - Volunteer registration
- `/volunteer` - Volunteer dashboard
- `/get_villages` - API for district villages
- `/predict_task` - ML task prediction

## Database Schema

### Workers Collection (Farmers)
```json
{
  "_id": "ObjectId",
  "name": "string",
  "password": "hashed_string",
  "location": "string",
  "coordinates": {"type": "Point", "coordinates": [lon, lat]},
  "district": "string",
  "village": "string",
  "accepted_tasks": ["task_ids"],
  "denied_tasks": ["task_ids"]
}
```

### Tasks Collection
```json
{
  "_id": "ObjectId",
  "location": "string",
  "task": "string",
  "village": "string",
  "district": "string",
  "coordinates": {"type": "Point", "coordinates": [lon, lat]},
  "wage": "number",
  "num_workers": "number",
  "description": "string",
  "posted_by": "volunteer_id",
  "accepted_by": "farmer_id",
  "denied_by": ["farmer_ids"],
  "status": "string",
  "created_at": "timestamp"
}
```

## Issues Fixed

### 1. Abstract vs Implementation Mismatch
- **Issue**: Abstract mentioned PostgreSQL/Firebase, code used MongoDB
- **Status**: Documented actual technology stack

### 2. Missing Core Features
- **Issue**: Wage, worker count, multilingual, voice input not implemented
- **Status**: ✅ All implemented

### 3. Geographic Data Issues
- **Issue**: All districts had same villages
- **Status**: ✅ Fixed with district-specific mappings

### 4. Distance Calculation
- **Issue**: No proper Haversine formula implementation
- **Status**: ✅ Implemented with precise 10km radius

### 5. Error Handling
- **Issue**: Basic error handling without user feedback
- **Status**: ✅ Comprehensive validation and messaging

## Future Enhancements

1. **Real-time Updates**: WebSocket implementation for live updates
2. **Full Multilingual**: Complete translation system
3. **Advanced Voice**: Voice commands for navigation
4. **Mobile App**: Native mobile application
5. **Payment Integration**: Digital payment system
6. **Rating System**: Worker and farmer rating system
7. **Notification System**: SMS/Email notifications
8. **Analytics Dashboard**: Usage statistics and insights

## Contributing

1. Fork the repository
2. Create a feature branch
3. Make your changes
4. Test thoroughly
5. Submit a pull request

## License

This project is licensed under the MIT License.

## Contact

For questions or support, please contact the development team.

---

**Note**: This project is designed specifically for rural agricultural communities in Telangana, India, with focus on digital inclusivity and community support through Grama Volunteers.
