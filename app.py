import eventlet
import json
import os
from datetime import datetime

# Patch standard Python libraries to be cooperative (non-blocking)
eventlet.monkey_patch()

from flask import Flask, render_template, request, session, redirect, url_for, flash
from flask_socketio import SocketIO, emit

# --- Admin Configuration ---
ADMIN_PIN = '32541'  # The specific PIN for admin login
ADMIN_ID = 'ADMIN'  # The identifier for the admin user

# --- Load Delegate Credentials from JSON ---
SITE_ROOT = os.path.realpath(os.path.dirname(__file__))
json_url = os.path.join(SITE_ROOT, "delegates.json")

try:
    with open(json_url, 'r') as f:
        DELEGATE_CREDENTIALS = json.load(f)
except FileNotFoundError:
    print("WARNING: delegates.json not found. Authentication will fail.")
    DELEGATE_CREDENTIALS = {}

# Use 'eventlet' for production deployment
app = Flask(__name__)
# *** IMPORTANT: SET A SECURE SECRET KEY FOR SESSIONS ***
app.config['SECRET_KEY'] = 'a_very_secure_secret_key_for_flask_sessions'
# Allow all origins for testing/development
socketio = SocketIO(app, async_mode='eventlet', cors_allowed_origins="*")

# Variable to hold the stream of updates. Each entry is a dict.
update_stream = []


# --- Helper Function to Render Stream (NO CHANGE) ---
def render_update_stream():
    """Renders the entire update_stream list into a readable HTML string."""
    html_content = ""
    if not update_stream:
        return "<p class='text-gray-500 italic'>No updates yet...</p>"

    for update in reversed(update_stream):
        timestamp = update['timestamp']
        delegate_id = update['id']
        update_type = update.get('type', 'message')

        if update_type == 'message':
            message = update['message']
            html_content += f"""
            <div class="p-3 bg-white rounded-lg shadow-md mb-3 border-l-4 border-blue-500 text-left">
                <p class="text-sm font-semibold text-gray-500">
                    <span class="text-blue-700 font-bold mr-2">[{delegate_id}]</span> 
                    <span class="float-right text-xs font-normal text-gray-400">{timestamp}</span>
                </p>
                <p class="text-gray-800 mt-1 whitespace-pre-wrap">{message}</p>
            </div>
            """
        elif update_type == 'vote':
            vote = update['vote']
            color_class = "border-green-500 bg-green-50" if vote == 'yay' else "border-red-500 bg-red-50"
            vote_emoji = "✅ YAY" if vote == 'yay' else "❌ NAY"

            html_content += f"""
            <div class="p-3 {color_class} rounded-lg shadow-md mb-3 border-l-4 text-left">
                <p class="text-sm font-bold text-gray-700">
                    <span class="text-lg font-extrabold mr-2">{vote_emoji}</span>
                    <span class="font-semibold mr-1">VOTE from</span>
                    <span class="font-extrabold text-lg text-black">[{delegate_id}]</span>
                    <span class="float-right text-xs font-normal text-gray-500 mt-1">{timestamp}</span>
                </p>
            </div>
            """
    return html_content


# --- NEW Routes for Login/Logout (MODIFIED to include Admin) ---

@app.route('/')
def index():
    """Default route redirects to login."""
    return redirect(url_for('login'))


@app.route('/login', methods=['GET', 'POST'])
def login():
    """Handles delegate and admin sign-in using a PIN."""
    if request.method == 'POST':
        pin = request.form.get('pin')

        # 1. Check for Admin PIN
        if pin == ADMIN_PIN:
            session['delegate_id'] = ADMIN_ID
            flash('Admin login successful.', 'success')
            return redirect(url_for('admin'))  # Redirect to the new admin page

        # 2. Check for Delegate PIN
        elif pin in DELEGATE_CREDENTIALS:
            delegate_id = DELEGATE_CREDENTIALS[pin]
            session['delegate_id'] = delegate_id
            return redirect(url_for('delegate', delegate_id=delegate_id))

        # 3. Invalid PIN
        else:
            flash('Invalid PIN. Please try again.', 'error')

    # GET request or failed POST
    return render_template('login.html')


@app.route('/logout')
def logout():
    """Logs the user out by clearing the session."""
    session.pop('delegate_id', None)
    flash('You have been logged out.', 'info')
    return redirect(url_for('login'))


# --- NEW Admin Routes ---

@app.route('/admin')
def admin():
    """Admin dashboard - view and clear stream."""
    # Check for ADMIN_ID in session
    if session.get('delegate_id') != ADMIN_ID:
        flash('Admin access required.', 'error')
        return redirect(url_for('login'))

    initial_content = render_update_stream()
    return render_template('admin.html', initial_content=initial_content)


@app.route('/admin/clear', methods=['POST'])
def clear_stream():
    """Clears the update stream and broadcasts the change."""
    global update_stream

    # Security check: Only allow ADMIN to clear
    if session.get('delegate_id') != ADMIN_ID:
        flash('Unauthorized attempt to clear stream.', 'error')
        return redirect(url_for('login'))

    update_stream = []  # Clear the stream!

    # Broadcast an empty stream to all connected clients (dashboard/admin)
    socketio.emit('stream_update', {'data': render_update_stream()}, broadcast=True)

    flash('Update stream successfully cleared!', 'success')
    return redirect(url_for('admin'))


# --- Existing Routes (NO CHANGE) ---

@app.route('/dashboard')
def dashboard():
    """Main dashboard page - displays live updates."""
    initial_content = render_update_stream()
    return render_template('dashboard.html', initial_content=initial_content)


@app.route('/delegate')
def delegate():
    """Delegate input page - sends the data."""
    # Check if delegate is logged in via session
    current_delegate_id = session.get('delegate_id')

    if not current_delegate_id or current_delegate_id == ADMIN_ID:
        flash('Please log in as a delegate to access the input.', 'error')
        return redirect(url_for('login'))

    return render_template('delegate.html', delegate_id=current_delegate_id)


# --- WebSocket Handlers (NO CHANGE) ---

@socketio.on('connect')
def handle_connect():
    """Handler for new client connections."""
    print('Client connected:', request.sid)
    current_stream_html = render_update_stream()
    emit('stream_update', {'data': current_stream_html}, room=request.sid)


@socketio.on('delegate_message')
def handle_delegate_message(data):
    """Handles a message event from the /delegate page."""
    global update_stream
    delegate_id = data.get('delegate_id', 'UNKNOWN')
    new_message = data.get('message', 'No message provided')
    timestamp = datetime.now().strftime('%H:%M:%S')

    new_update = {
        'id': delegate_id.upper(),
        'message': new_message,
        'timestamp': timestamp,
        'type': 'message'
    }
    update_stream.append(new_update)
    print(f"[{timestamp}] New Update from {delegate_id}: {new_message}")
    updated_stream_html = render_update_stream()
    emit('stream_update', {'data': updated_stream_html}, broadcast=True)


@socketio.on('delegate_vote')
def handle_delegate_vote(data):
    """Handles a vote event from the /delegate page."""
    global update_stream
    delegate_id = data.get('delegate_id', 'UNKNOWN')
    vote = data.get('vote', 'unknown')
    timestamp = datetime.now().strftime('%H:%M:%S')

    new_update = {
        'id': delegate_id.upper(),
        'vote': vote,
        'timestamp': timestamp,
        'type': 'vote'
    }
    update_stream.append(new_update)
    print(f"[{timestamp}] New Vote from {delegate_id}: {vote}")
    updated_stream_html = render_update_stream()
    emit('stream_update', {'data': updated_stream_html}, broadcast=True)


# --- Server Start (NO CHANGE) ---

if __name__ == '__main__':
    print("Server running. Access dashboard at http://127.0.0.1:5000/dashboard")
    print("Access delegate login at http://127.0.0.1:5000/login")
    socketio.run(app, debug=True, port=5000)