#!/usr/bin/env python3
import socket
import sys

PORT = 50000

SURR_MODES = {
    "dolby_surround": 0x01,
    "dts_neuralx": 0x02,
    "stereo_2.0": 0x03,
    "stereo_2.1": 0x04,
    "all_stereo": 0x05,
    "native": 0x06,
    "dolby_prologic_ii": 0x07,
}

def build_cmd(cmd_id: int, data1: int = None) -> bytes:
    if data1 is None:
        return bytes([0x23, cmd_id, 0x00, 0x0D])
    return bytes([0x23, cmd_id, 0x01, data1, 0x0D])

def send_cmd(sock: socket.socket, cmd_id: int, data1: int = None) -> bytes:
    cmd = build_cmd(cmd_id, data1)
    sock.send(cmd)
    return sock.recv(1024)

def init_connection(sock: socket.socket) -> tuple[bool, bytes]:
    resp = send_cmd(sock, 0x50, 0x01)
    return len(resp) >= 6 and resp[3] == 0x01, resp

def set_surround_mode(host: str, mode: str) -> bool:
    mode_val = SURR_MODES.get(mode.lower().replace(" ", "_"))
    if mode_val is None:
        print(f"Unknown mode: {mode}")
        print(f"Available: {', '.join(SURR_MODES.keys())}")
        return False

    sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    try:
        sock.settimeout(5)
        sock.connect((host, PORT))
        print(f"Connected to {host}:{PORT}")

        ok, resp = init_connection(sock)
        print(f"Init response: {resp.hex()}")
        if ok:
            print("Init OK")
        else:
            print("Init response unexpected, but continuing...")

        print(f"Setting surround mode to {mode} (0x{mode_val:02x})...")
        resp = send_cmd(sock, 0x08, mode_val)
        print(f"Response: {resp.hex()}")

        if len(resp) >= 6 and resp[3] == 0x00 and resp[5] == mode_val:
            print(f"Success! Mode set to {mode}")
            return True
            return True
        else:
            print(f"Unexpected response")
            return False
    except socket.timeout:
        print("Error: Connection timed out. Is the receiver on and accessible?")
        return False
    except ConnectionRefusedError:
        print("Error: Connection refused. Is IP control enabled on the receiver?")
        return False
    except Exception as e:
        print(f"Error: {e}")
        return False
    finally:
        sock.close()

if __name__ == "__main__":
    if len(sys.argv) < 2:
        print(f"Usage: {sys.argv[0]} <receiver_ip> [mode]")
        print(f"Modes: {', '.join(SURR_MODES.keys())}")
        sys.exit(1)
    host = sys.argv[1]
    mode = sys.argv[2] if len(sys.argv) > 2 else "all_stereo"
    set_surround_mode(host, mode)