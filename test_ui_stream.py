import requests
import json

url = "http://localhost:8000/v1/chat/completions"
payload = {"session_id": "test_stream_session", "message": "프레임워크의 목적이 뭐야?"}

print("Initiating SSE request...")
found_telemetry = False
found_token = False

try:
    with requests.post(url, json=payload, stream=True) as r:
        for line in r.iter_lines():
            if line:
                decoded_line = line.decode('utf-8')
                if decoded_line.startswith("data: "):
                    data = json.loads(decoded_line[6:])
                    print("Received chunk:", data)
                    if data["type"] == "telemetry": found_telemetry = True
                    if data["type"] == "token": found_token = True
                    
    if found_telemetry and found_token:
        print("[PASS] Streaming API bridge fully operational.")
    else:
        print("[FAIL] Missing expected SSE packets.")
except Exception as e:
    print(f"[ERROR] {e}")
