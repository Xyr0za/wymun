import eventlet
# Patch standard Python libraries to be cooperative (non-blocking)
eventlet.monkey_patch()

from flask import Flask, render_template, request
from flask_socketio import SocketIO, emit

# Use 'eventlet' for production deployment
app = Flask(__name__)
# IMPORTANT: Use a secure secret key in production
app.config['SECRET_KEY'] = 'super_secret'
# Allow all origins for testing/development
socketio = SocketIO(app, async_mode='eventlet', cors_allowed_origins="*")

# Variable to hold the text to display on the dashboard
live_text = "Waiting for delegate input..."


# --- Routes ---

@app.route('/dashboard')
def dashboard():
    """Main dashboard page - displays live updates."""
    # Pass the current text to the template on initial load
    return render_template('dashboard.html', current_text=live_text)


@app.route('/delegate')
def delegate():
    """Delegate input page - sends the data."""
    return render_template('delegate.html')


# --- WebSocket Handlers ---

@socketio.on('connect')
def handle_connect():
    """Handler for new client connections."""
    print('Client connected:', request.sid)
    # Automatically send the current status to a newly connected client
    emit('update_text', {'data': live_text}, room=request.sid)


@socketio.on('delegate_message')
def handle_delegate_message(data):
    """
    Handles a message event from the /delegate page (via WebSocket).
    data should contain a 'message' key.
    """
    global live_text
    new_message = data.get('message', 'No message provided')
    live_text = f"**Delegate Update**: {new_message}"

    print(f"Received WebSocket message: {new_message}")

    # Send the update to ALL connected clients (i.e., the dashboard page)
    # The client-side JS will listen for the 'update_text' event.
    emit('update_text', {'data': live_text}, broadcast=True)


# --- POST Request Alternative (Removed/Ignored for this WS-focused setup) ---
# NOTE: The POST route from your original code is kept as a comment for context,
# but the primary live communication happens via the 'delegate_message' event.
# @app.route('/post-update', methods=['POST'])
# def handle_post_update():
#     ...

# --- Server Start ---

if __name__ == '__main__':
    # Use socketio.run instead of app.run to include the WebSocket server
    print("Server running. Access dashboard at http://127.0.0.1:5000/dashboard")
    print("Access delegate input at http://127.0.0.1:5000/delegate")
    socketio.run(app, debug=True, port=5000)