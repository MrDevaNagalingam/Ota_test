#!/usr/bin/env python3
import serial
import time
import json
import os
import requests
from struct import pack, unpack
from datetime import datetime
import threading

# Configuration
SERIAL_PORT = "/dev/ttymxc2"  # UART3
BAUD_RATE = 57600
ADDRESS = b'\xFF\xFF\xFF\xFF'
PASSWORD = b'\x00\x00\x00\x00'
FINGERPRINTS_FILE = "fingerprints.json"
ATTENDANCE_FILE = "attendance.json"
DEVICE_ID = "DEVICE_001"  # Unique identifier for this device

# Server Configuration
SERVER_URL = "http://192.168.1.4:8081"  # Change to your server IP
SYNC_ENABLED = True  # Set to False to disable server sync

# RGB LED Colors for R503
LED_COLORS = {
    'RED': 0x01,
    'BLUE': 0x02,
    'PURPLE': 0x03,
    'GREEN': 0x04,
    'YELLOW': 0x05,
    'CYAN': 0x06,
    'WHITE': 0x07
}

# Load stored fingerprints and attendance data
def load_data():
    fingerprints = {}
    attendance = {}
    
    if os.path.exists(FINGERPRINTS_FILE):
        with open(FINGERPRINTS_FILE, "r") as f:
            fingerprints = json.load(f)
    
    if os.path.exists(ATTENDANCE_FILE):
        with open(ATTENDANCE_FILE, "r") as f:
            attendance = json.load(f)
    
    return fingerprints, attendance

def save_fingerprints(fingerprints):
    with open(FINGERPRINTS_FILE, "w") as f:
        json.dump(fingerprints, f, indent=2)

def save_attendance(attendance):
    with open(ATTENDANCE_FILE, "w") as f:
        json.dump(attendance, f, indent=2)

# Load initial data
fingerprint_names, attendance_log = load_data()

# Open serial port
ser = serial.Serial(SERIAL_PORT, BAUD_RATE, timeout=2)

# Send command packet
def send_packet(pid, payload):
    packet = b'\xEF\x01' + ADDRESS + bytes([pid])
    length = len(payload) + 2
    packet += pack('>H', length) + payload
    checksum = sum(payload) + pid + (length >> 8) + (length & 0xFF)
    packet += pack('>H', checksum)
    ser.write(packet)

# Read response
def read_response():
    header = ser.read(9)
    if len(header) < 9:
        return None, None
    pid = header[6]
    length = unpack('>H', header[7:9])[0]
    content = ser.read(length)
    return pid, content

# Verify sensor password
def verify_password():
    send_packet(0x01, b'\x13' + PASSWORD)
    pid, content = read_response()
    if content:
        return content[0] == 0x00
    return False

# R503 LED Control with RGB colors
def r503_led_control(color='BLUE', mode=1, speed=0x80, cycle=1):
    """
    Control R503 RGB LED
    mode: 1=breathing, 2=flashing, 3=always on, 4=always off, 5=fade in, 6=fade out
    speed: 0x00-0xFF (speed of effect)
    cycle: number of cycles (0=infinite)
    """
    try:
        color_code = LED_COLORS.get(color.upper(), LED_COLORS['BLUE'])
        payload = b'\x35' + bytes([mode, speed, color_code, cycle])
        send_packet(0x01, payload)
        pid, content = read_response()
        return content and content[0] == 0x00
    except Exception as e:
        print(f"LED control error: {e}")
        return False

# LED patterns for different actions
def led_pattern_scanning():
    r503_led_control('BLUE', mode=1, speed=0x80, cycle=0)  # Blue breathing

def led_pattern_success():
    r503_led_control('GREEN', mode=2, speed=0x50, cycle=3)  # Green flashing 3 times

def led_pattern_error():
    r503_led_control('RED', mode=2, speed=0x30, cycle=5)  # Red flashing 5 times

def led_pattern_registration():
    r503_led_control('PURPLE', mode=1, speed=0x60, cycle=0)  # Purple breathing

def led_pattern_idle():
    r503_led_control('BLUE', mode=3, speed=0x80, cycle=0)  # Blue always on

def led_off():
    r503_led_control('BLUE', mode=4, speed=0x00, cycle=0)  # Turn off

# Server synchronization functions
def sync_to_server(data_type, data):
    """Sync data to server"""
    if not SYNC_ENABLED:
        return True
    
    try:
        url = f"{SERVER_URL}/api/{data_type}"
        headers = {'Content-Type': 'application/json'}
        data['device_id'] = DEVICE_ID
        
        response = requests.post(url, json=data, headers=headers, timeout=5)
        return response.status_code == 200
    except Exception as e:
        print(f"Server sync error: {e}")
        return False

def sync_fingerprints_from_server():
    """Download fingerprint database from server"""
    if not SYNC_ENABLED:
        return fingerprint_names
    
    try:
        url = f"{SERVER_URL}/api/fingerprints"
        response = requests.get(url, timeout=5)
        if response.status_code == 200:
            server_data = response.json()
            return server_data.get('fingerprints', {})
    except Exception as e:
        print(f"Server sync error: {e}")
    
    return fingerprint_names

def sync_attendance_record(user_id, name, action):
    """Sync attendance record to server"""
    current_time = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    data = {
        'user_id': user_id,
        'name': name,
        'action': action,  # 'check_in' or 'check_out'
        'timestamp': current_time,
        'device_id': DEVICE_ID
    }
    
    return sync_to_server('attendance', data)

# Delete fingerprint from sensor
def delete_fingerprint(page_id):
    payload = b'\x0C' + pack('>H', page_id) + pack('>H', 1)
    send_packet(0x01, payload)
    pid, content = read_response()
    return content and content[0] == 0x00

# Try to get fingerprint image
def gen_image():
    send_packet(0x01, b'\x01')
    pid, content = read_response()
    if not content:
        return False
    code = content[0]
    if code == 0x00:
        return True
    elif code == 0x02:
        return False  # No finger
    elif code == 0x03:
        print("Failed to capture finger image.")
        return False
    else:
        print(f"gen_image error code: {hex(code)}")
        return False

# Convert image to template in buffer
def image2tz(buf_id):
    send_packet(0x01, b'\x02' + bytes([buf_id]))
    pid, content = read_response()
    return content and content[0] == 0x00

# Combine templates into model
def create_model():
    send_packet(0x01, b'\x05')
    pid, content = read_response()
    return content and content[0] == 0x00

# Store fingerprint template in flash
def store_model(buf_id, position):
    payload = b'\x06' + bytes([buf_id]) + pack('>H', position)
    send_packet(0x01, payload)
    pid, content = read_response()
    return content and content[0] == 0x00

# Search for matching fingerprint
def search_fingerprint():
    payload = b'\x04\x01' + pack('>H', 0) + pack('>H', 200)
    send_packet(0x01, payload)
    pid, content = read_response()
    if content and content[0] == 0x00:
        page_id = unpack('>H', content[1:3])[0]
        return page_id
    return None

# Find next unused ID from sensor
def get_next_available_id():
    """Find next available ID by checking sensor memory"""
    stored_template_ids = read_template_table()
    
    for i in range(200):  # R503 supports up to 200 templates
        if i not in stored_template_ids:
            return i
    return None

# Record attendance with server sync
def record_attendance(user_id, name):
    current_time = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    date = datetime.now().strftime("%Y-%m-%d")
    
    # Initialize attendance structure
    if date not in attendance_log:
        attendance_log[date] = {}
    
    # Determine check-in or check-out
    if user_id not in attendance_log[date]:
        # First scan today = check-in
        attendance_log[date][user_id] = {
            "name": name,
            "check_in": current_time,
            "check_out": None
        }
        action = "check_in"
        print(f"Check-in recorded for {name} at {current_time}")
        led_pattern_success()
    else:
        # Second scan today = check-out
        attendance_log[date][user_id]["check_out"] = current_time
        action = "check_out"
        print(f"Check-out recorded for {name} at {current_time}")
        led_pattern_success()
    
    # Save local attendance
    save_attendance(attendance_log)
    
    # Sync to server
    if not sync_attendance_record(user_id, name, action):
        print("Warning: Failed to sync attendance to server")

# Display menu
def display_menu():
    print("\n" + "="*50)
    print("FINGERPRINT ATTENDANCE SYSTEM")
    print(f"Device ID: {DEVICE_ID}")
    print("="*50)
    print("1. Scan fingerprint")
    print("2. Register new fingerprint")
    print("3. List all fingerprints")
    print("4. Delete fingerprint (from sensor)")
    print("5. View attendance log")
    print("6. Sync with server")
    print("7. Clean up database")
    print("8. Exit")
    print("="*50)

# Get template count from sensor
def get_template_count():
    """Get number of stored templates in sensor"""
    send_packet(0x01, b'\x1D')
    pid, content = read_response()
    if content and content[0] == 0x00:
        return unpack('>H', content[1:3])[0]
    return 0

# Read template indices from sensor
def read_template_table():
    """Read template table from sensor to get all stored template IDs"""
    template_ids = []
    
    # Read template table (page by page)
    for page in range(4):  # R503 has 4 pages of template table
        payload = b'\x1F' + bytes([page])
        send_packet(0x01, payload)
        pid, content = read_response()
        
        if content and content[0] == 0x00:
            # Each byte represents 8 template slots (bits)
            table_data = content[1:]
            for byte_idx, byte_val in enumerate(table_data):
                for bit_idx in range(8):
                    if byte_val & (1 << bit_idx):
                        template_id = page * 256 + byte_idx * 8 + bit_idx
                        if template_id < 200:  # R503 supports up to 200 templates
                            template_ids.append(template_id)
    
    return template_ids

# List all registered fingerprints from sensor
def list_fingerprints():
    print("\nReading fingerprints from sensor...")
    
    # Get template count
    template_count = get_template_count()
    print(f"Total templates in sensor: {template_count}")
    
    if template_count == 0:
        print("No fingerprints stored in sensor.")
        return
    
    # Get all template IDs from sensor
    stored_template_ids = read_template_table()
    
    if not stored_template_ids:
        print("No fingerprints found in sensor memory.")
        return
    
    print("\nFingerprints stored in sensor:")
    print("=" * 40)
    print("ID  | Name                | Status")
    print("-" * 40)
    
    for template_id in sorted(stored_template_ids):
        # Get name from local database if available
        name = fingerprint_names.get(str(template_id), "Unknown User")
        status = "Named" if str(template_id) in fingerprint_names else "Unnamed"
        print(f"{str(template_id).ljust(3)} | {name.ljust(18)} | {status}")
    
    print("-" * 40)
    print(f"Total: {len(stored_template_ids)} fingerprints in sensor")
    
    # Check for orphaned entries in local database
    orphaned_entries = []
    for user_id in fingerprint_names:
        if int(user_id) not in stored_template_ids:
            orphaned_entries.append(user_id)
    
    if orphaned_entries:
        print(f"\nWarning: {len(orphaned_entries)} entries in local database not found in sensor:")
        for user_id in orphaned_entries:
            print(f"  ID {user_id}: {fingerprint_names[user_id]}")
        print("Consider cleaning up local database.")

# Delete fingerprint
def delete_fingerprint_menu():
    print("\nReading fingerprints from sensor...")
    
    # Get stored template IDs from sensor
    stored_template_ids = read_template_table()
    
    if not stored_template_ids:
        print("No fingerprints found in sensor.")
        return
    
    print("\nFingerprints available for deletion:")
    print("=" * 40)
    for template_id in sorted(stored_template_ids):
        name = fingerprint_names.get(str(template_id), "Unknown User")
        print(f"ID: {template_id} | Name: {name}")
    print("=" * 40)
    
    try:
        user_input = input("\nEnter ID to delete: ").strip()
        user_id = int(user_input)
        
        if user_id not in stored_template_ids:
            print("Invalid ID. Fingerprint not found in sensor.")
            return
        
        name = fingerprint_names.get(str(user_id), "Unknown User")
        confirm = input(f"Delete fingerprint for '{name}' (ID: {user_id})? (y/n): ").strip().lower()
        
        if confirm == 'y':
            if delete_fingerprint(user_id):
                # Remove from local database if exists
                if str(user_id) in fingerprint_names:
                    del fingerprint_names[str(user_id)]
                    save_fingerprints(fingerprint_names)
                
                # Sync deletion to server
                sync_data = {
                    'action': 'delete',
                    'user_id': str(user_id),
                    'name': name
                }
                sync_to_server('fingerprints', sync_data)
                
                print(f"Fingerprint for '{name}' (ID: {user_id}) deleted successfully from sensor.")
                led_pattern_success()
            else:
                print("Failed to delete fingerprint from sensor.")
                led_pattern_error()
        else:
            print("Deletion cancelled.")
    except ValueError:
        print("Invalid input. Please enter a valid number.")

# View attendance log
def view_attendance():
    if not attendance_log:
        print("No attendance records found.")
        return
    
    print("\nAttendance Log:")
    print("=" * 60)
    
    for date in sorted(attendance_log.keys(), reverse=True):
        print(f"\nDate: {date}")
        print("-" * 40)
        
        for user_id, record in attendance_log[date].items():
            name = record["name"]
            check_in = record["check_in"]
            check_out = record["check_out"] if record["check_out"] else "Not checked out"
            
            print(f"Name: {name}")
            print(f"Check-in: {check_in}")
            print(f"Check-out: {check_out}")
            print("-" * 40)

# Sync with server
def sync_with_server():
    print("Syncing with server...")
    
    # Download latest fingerprint database
    global fingerprint_names
    server_fingerprints = sync_fingerprints_from_server()
    
    if server_fingerprints:
        fingerprint_names.update(server_fingerprints)
        save_fingerprints(fingerprint_names)
        print("Fingerprint database synced successfully.")
    else:
        print("Failed to sync fingerprint database.")
    
    # Upload local attendance records
    sync_count = 0
    for date, records in attendance_log.items():
        for user_id, record in records.items():
            # Sync check-in
            if record.get("check_in"):
                data = {
                    'user_id': user_id,
                    'name': record['name'],
                    'action': 'check_in',
                    'timestamp': record['check_in']
                }
                if sync_to_server('attendance', data):
                    sync_count += 1
            
            # Sync check-out
            if record.get("check_out"):
                data = {
                    'user_id': user_id,
                    'name': record['name'],
                    'action': 'check_out',
                    'timestamp': record['check_out']
                }
                if sync_to_server('attendance', data):
                    sync_count += 1
    
# Clean up local database
def clean_up_database():
    """Remove entries from local database that don't exist in sensor"""
    print("\nChecking database consistency with sensor...")
    
    stored_template_ids = read_template_table()
    orphaned_entries = []
    
    for user_id in list(fingerprint_names.keys()):
        if int(user_id) not in stored_template_ids:
            orphaned_entries.append(user_id)
    
    if not orphaned_entries:
        print("Database is clean. No orphaned entries found.")
        return
    
    print(f"\nFound {len(orphaned_entries)} orphaned entries in local database:")
    for user_id in orphaned_entries:
        print(f"  ID {user_id}: {fingerprint_names[user_id]}")
    
    confirm = input(f"\nRemove {len(orphaned_entries)} orphaned entries? (y/n): ").strip().lower()
    
    if confirm == 'y':
        for user_id in orphaned_entries:
            del fingerprint_names[user_id]
        
        save_fingerprints(fingerprint_names)
        print(f"Removed {len(orphaned_entries)} orphaned entries from database.")
        led_pattern_success()
    else:
        print("Database cleanup cancelled.")

# Register new fingerprint
def register_fingerprint():
    print("\nRegistering new fingerprint...")
    name = input("Enter name: ").strip()
    if not name:
        print("Name cannot be empty.")
        return False
    
    new_id = get_next_available_id()
    if new_id is None:
        print("Storage full. Cannot register new fingerprint.")
        return False
    
    print("Place finger on sensor...")
    led_pattern_registration()
    
    # First scan
    retries = 0
    scanned = False
    while retries < 50:
        if gen_image():
            scanned = True
            break
        time.sleep(0.2)
        retries += 1
    
    if not scanned or not image2tz(1):
        print("Failed to capture first fingerprint.")
        led_pattern_error()
        return False
    
    print("Remove finger and place the same finger again...")
    time.sleep(2)
    led_pattern_registration()
    
    # Second scan
    retries = 0
    scanned = False
    while retries < 50:
        if gen_image():
            scanned = True
            break
        time.sleep(0.2)
        retries += 1
    
    if not scanned or not image2tz(2):
        print("Failed to capture second fingerprint.")
        led_pattern_error()
        return False
    
    if not create_model():
        print("Failed to create fingerprint model.")
        led_pattern_error()
        return False
    
    if store_model(1, new_id):
        fingerprint_names[str(new_id)] = name
        save_fingerprints(fingerprint_names)
        
        # Sync to server
        sync_data = {
            'action': 'register',
            'user_id': str(new_id),
            'name': name
        }
        sync_to_server('fingerprints', sync_data)
        
        print(f"Fingerprint registered successfully for '{name}' (ID: {new_id})")
        led_pattern_success()
        return True
    else:
        print("Failed to store fingerprint.")
        led_pattern_error()
        return False

# Scan fingerprint
def scan_fingerprint():
    print("\nPlace finger on sensor...")
    led_pattern_scanning()
    
    retries = 0
    scanned = False
    while retries < 50:
        if gen_image():
            scanned = True
            break
        time.sleep(0.2)
        retries += 1
    
    if not scanned:
        print("No finger detected. Try again.")
        led_pattern_error()
        return
    
    if not image2tz(1):
        print("Failed to convert image to character file.")
        led_pattern_error()
        return
    
    match_id = search_fingerprint()
    if match_id is not None:
        name = fingerprint_names.get(str(match_id), "Unknown")
        print(f"Welcome, {name} (ID: {match_id})")
        record_attendance(str(match_id), name)
    else:
        print("Fingerprint not recognized.")
        led_pattern_error()

# Main program
def main():
    print("Connecting to R503 fingerprint sensor...")
    
    if not verify_password():
        print("Sensor password verification failed.")
        return
    
    print("Sensor ready.")
    
    # Set initial LED state
    led_pattern_idle()
    
    # Sync with server on startup
    if SYNC_ENABLED:
        print("Syncing with server on startup...")
        sync_with_server()
    
    while True:
        display_menu()
        
        try:
            choice = input("\nEnter your choice (1-8): ").strip()
            
            if choice == '1':
                scan_fingerprint()
            elif choice == '2':
                register_fingerprint()
            elif choice == '3':
                list_fingerprints()
            elif choice == '4':
                delete_fingerprint_menu()
            elif choice == '5':
                view_attendance()
            elif choice == '6':
                sync_with_server()
            elif choice == '7':
                clean_up_database()
            elif choice == '8':
                print("Exiting...")
                break
            else:
                print("Invalid choice. Please try again.")
                
        except KeyboardInterrupt:
            print("\nExiting...")
            break
        except Exception as e:
            print(f"Error: {e}")
        
        # Return to idle state
        led_pattern_idle()
        input("\nPress Enter to continue...")

if __name__ == "__main__":
    try:
        main()
    finally:
        # Cleanup
        led_off()
        ser.close()
        print("System shutdown complete.")
