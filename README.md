# Smart Lost and Found System for College

A Flask + SQLite web application. Students can post lost/found items, and MobileNetV2 extracts image features to find visually similar reports using cosine similarity. Potential matches create in-app notifications for both students.

## Requirements
- Windows 10/11
- Python 3.11 (recommended) or Python 3.12
- Internet connection on first launch (TensorFlow downloads MobileNetV2 ImageNet weights)
- At least 4 GB RAM; 8 GB recommended for TensorFlow

## Quick start
1. Extract the project ZIP into a normal folder.
2. Install Python 3.11 or 3.12 and select **Add Python to PATH**.
3. Double-click `run.bat`.
4. Wait for packages to install. The first start may take longer while TensorFlow downloads model weights.
5. Open http://127.0.0.1:5000 if the browser does not open automatically.
6. Register a student account and sign in.

## Manual setup
```bat
py -3.11 -m venv .venv
.venv\Scripts\activate
python -m pip install --upgrade pip
pip install -r requirements.txt
python app.py
```

## How to use
1. Register with your name, college email, student ID and password.
2. Post a **Lost item** report or a **Found item** report. Use a clear photo.
3. The application computes a MobileNetV2 feature vector and compares it with existing reports of the opposite type.
4. If cosine similarity is at least 0.62, both students receive an in-app notification.
5. Open the notification or item page to inspect the possible match and contact the other student using the displayed email.
6. Mark your own post resolved from your dashboard once the item is returned.

## Project structure
```text
smart_lost_found_college/
├── app.py
├── requirements.txt
├── run.bat
├── README.md
├── lost_found.db          # created automatically; local data is not committed
├── templates/
│   ├── base.html
│   ├── index.html
│   ├── auth.html
│   ├── dashboard.html
│   ├── item_form.html
│   ├── items.html
│   ├── item_detail.html
│   ├── notifications.html
│   └── error.html
└── static/
    ├── css/style.css
    ├── js/app.js
    └── uploads/           # uploaded images are saved here; images are not committed
```

## Notes and limitations
- MobileNetV2 is a general-purpose ImageNet feature extractor, not a dedicated lost-property model. Similarity is a visual hint, not proof that two photos show the same physical object.
- The 0.62 similarity threshold is a starting point. It should be calibrated using a representative set of college item photos; lighting, angle, background and occlusion affect scores.
- Matching runs when a new post is submitted. Older records are not reprocessed automatically.
- In-app notifications are shown inside this website. Email/SMS delivery is not included.
- This starter project has student accounts but no admin panel, as requested.
- For real deployment, change `SECRET_KEY`, disable Flask debug mode, add CSRF protection, rate limiting, image moderation, backups and secure hosting. Do not expose the Flask development server directly to the public internet.
- The database and uploaded images are local to the machine. Other students on different devices can use it only after deployment on a reachable server and suitable network configuration.
