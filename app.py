import eventlet

# Patch standard Python libraries to be cooperative (non-blocking)
eventlet.monkey_patch()

from flask import Flask, render_template, request
from flask_socketio import SocketIO, emit
from datetime import datetime

# Use 'eventlet' for production deployment
app = Flask(__name__)
# IMPORTANT: Use a secure secret key in production
app.config['SECRET_KEY'] = 'super_secret'
# Allow all origins for testing/development
socketio = SocketIO(app, async_mode='eventlet', cors_allowed_origins="*")

# Variable to hold the stream of updates. Each entry is a dict.
# e.g., [{'id': 'USA', 'message': 'Update 1', 'timestamp': '15:30:00', 'type': 'message'}, ...]
update_stream = []


# --- Helper Function to Render Stream ---

def render_update_stream():
    """Renders the entire update_stream list into a readable HTML string."""
    # We display the newest update at the top (reverse the list)
    html_content = ""
    if not update_stream:
        return "<p class='text-gray-500 italic'>No updates yet...</p>"

    for update in reversed(update_stream):
        timestamp = update['timestamp']
        delegate_id = update['id']
        update_type = update.get('type', 'message')  # Default to 'message' for old entries

        # --- Message Type Rendering ---
        if update_type == 'message':
            message = update['message']
            # Use Tailwind classes for styling each text update
            html_content += f"""
            <div class="p-3 bg-white rounded-lg shadow-md mb-3 border-l-4 border-blue-500 text-left">
                <p class="text-sm font-semibold text-gray-500">
                    <span class="text-blue-700 font-bold mr-2">[{delegate_id}]</span> 
                    <span class="float-right text-xs font-normal text-gray-400">{timestamp}</span>
                </p>
                <p class="text-gray-800 mt-1 whitespace-pre-wrap">{message}</p>
            </div>
            """
        # --- Vote Type Rendering ---
        elif update_type == 'vote':
            vote = update['vote']  # 'yay' or 'nay'
            color_class = "border-green-500 bg-green-50" if vote == 'yay' else "border-red-500 bg-red-50"
            vote_emoji = "✅ YAY" if vote == 'yay' else "❌ NAY"

            # Use Tailwind classes for styling each vote update
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


# --- Routes (NO CHANGE) ---

@app.route('/dashboard')
def dashboard():
    """Main dashboard page - displays live updates."""
    # Pass the initial rendered stream to the template
    initial_content = render_update_stream()
    return render_template('dashboard.html', initial_content=initial_content)


@app.route('/delegate')
def delegate():
    """Delegate input page - sends the data."""
    return render_template('delegate.html')


# --- WebSocket Handlers ---

@socketio.on('connect')
def handle_connect():
    """Handler for new client connections."""
    print('Client connected:', request.sid)
    # Send the ENTIRE current stream to a newly connected client
    current_stream_html = render_update_stream()
    emit('stream_update', {'data': current_stream_html}, room=request.sid)


@socketio.on('delegate_message')
def handle_delegate_message(data):
    """
    Handles a message event from the /delegate page (via WebSocket).
    data should contain 'delegate_id' and 'message' keys.
    """
    global update_stream

    delegate_id = data.get('delegate_id', 'UNKNOWN')
    new_message = data.get('message', 'No message provided')
    timestamp = datetime.now().strftime('%H:%M:%S')

    new_update = {
        'id': delegate_id.upper(),  # Standardize ID
        'message': new_message,
        'timestamp': timestamp,
        'type': 'message'  # New type field
    }

    # Add the new update to the stream list
    update_stream.append(new_update)

    print(f"[{timestamp}] New Update from {delegate_id}: {new_message}")

    # Send the ENTIRE updated stream to ALL connected clients
    updated_stream_html = render_update_stream()
    emit('stream_update', {'data': updated_stream_html}, broadcast=True)


@socketio.on('delegate_vote')
def handle_delegate_vote(data):
    """
    Handles a vote event from the /delegate page (via WebSocket).
    data should contain 'delegate_id' and 'vote' keys ('yay' or 'nay').
    """
    global update_stream

    delegate_id = data.get('delegate_id', 'UNKNOWN')
    vote = data.get('vote', 'unknown')
    timestamp = datetime.now().strftime('%H:%M:%S')

    new_update = {
        'id': delegate_id.upper(),  # Standardize ID
        'vote': vote,
        'timestamp': timestamp,
        'type': 'vote'  # New type field
    }

    # Add the new update to the stream list
    update_stream.append(new_update)

    print(f"[{timestamp}] New Vote from {delegate_id}: {vote}")

    # Send the ENTIRE updated stream to ALL connected clients
    updated_stream_html = render_update_stream()
    emit('stream_update', {'data': updated_stream_html}, broadcast=True)


# --- Server Start (NO CHANGE) ---

if __name__ == '__main__':
    # Use socketio.run instead of app.run to include the WebSocket server
    print("Server running. Access dashboard at http://127.0.0.1:5000/dashboard")
    print("Access delegate input at http://127.0.0.1:5000/delegate")
    socketio.run(app, debug=True, port=5000)