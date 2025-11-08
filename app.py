import eventlet
import json
import os  # Added for path manipulation

# Patch standard Python libraries to be cooperative (non-blocking)
eventlet.monkey_patch()

from flask import Flask, render_template, request, session, redirect, url_for, flash
from flask_socketio import SocketIO, emit
from datetime import datetime

# --- Load Delegate Credentials from JSON ---
# Determine the path to the delegates.json file
SITE_ROOT = os.path.realpath(os.path.dirname(__file__))
json_url = os.path.join(SITE_ROOT, "delegates.json")

try:
    with open(json_url, 'r') as f:
        # PIN to Delegate ID/Country mapping
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
    # ... (Your existing render_update_stream function content goes here) ...
    # This function remains unchanged from the original code provided in the prompt.
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


# --- NEW Routes for Login/Logout ---

@app.route('/')
def index():
    """Default route redirects to login."""
    return redirect(url_for('login'))


@app.route('/login', methods=['GET', 'POST'])
def login():
    """Handles delegate sign-in using a PIN."""
    if request.method == 'POST':
        pin = request.form.get('pin')

        # Check if PIN is in the credentials
        if pin in DELEGATE_CREDENTIALS:
            delegate_id = DELEGATE_CREDENTIALS[pin]
            # Store the delegate ID in the session
            session['delegate_id'] = delegate_id

            # Redirect to the main delegate input page with the ID in the URL
            # The URL parameter ensures the ID is visually present and easily accessible.
            return redirect(url_for('delegate', delegate_id=delegate_id))
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


# --- Existing Routes (MODIFIED) ---

@app.route('/dashboard')
def dashboard():
    """Main dashboard page - displays live updates (NO CHANGE)."""
    initial_content = render_update_stream()
    return render_template('dashboard.html', initial_content=initial_content)


@app.route('/delegate')
def delegate():
    """Delegate input page - sends the data (MODIFIED)."""
    # Check if delegate is logged in via session
    if 'delegate_id' not in session:
        flash('Please log in to access the delegate input.', 'error')
        return redirect(url_for('login'))

    # Get ID from session and use it in the template
    current_delegate_id = session['delegate_id']
    return render_template('delegate.html', delegate_id=current_delegate_id)


# --- WebSocket Handlers (MINOR MODIFICATION) ---

# The socket handlers remain largely the same, but the client-side code will now
# send the ID from the URL, which then gets standardized to uppercase in the handler.

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

    # The delegate_id is now retrieved directly from the data sent by the client.
    delegate_id = data.get('delegate_id', 'UNKNOWN')
    new_message = data.get('message', 'No message provided')
    timestamp = datetime.now().strftime('%H:%M:%S')

    new_update = {
        'id': delegate_id.upper(),  # Standardize ID
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

    # The delegate_id is now retrieved directly from the data sent by the client.
    delegate_id = data.get('delegate_id', 'UNKNOWN')
    vote = data.get('vote', 'unknown')
    timestamp = datetime.now().strftime('%H:%M:%S')

    new_update = {
        'id': delegate_id.upper(),  # Standardize ID
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