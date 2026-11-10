"""Run the local META demo with settings confined to this Python process."""
import ipaddress
import os
from pathlib import Path
import socket
import sys


def main():
    lan_ip = str(ipaddress.IPv4Address(sys.argv[1]))
    port = int(sys.argv[2])
    if not 1 <= port <= 65535:
        raise ValueError("Invalid port")
    root = Path(__file__).resolve().parent
    os.chdir(root)
    for name in ("main.py", "requirements.txt", "diploma.db"):
        if not (root / name).is_file():
            raise FileNotFoundError(name)
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as probe:
        if probe.connect_ex(("127.0.0.1", port)) == 0:
            print(f"Порт {port} уже занят: вероятно, META уже запущена (http://127.0.0.1:{port}/). "
                  f"Закройте прежнее окно или запустите на другом порту: start.bat -Port 8003")
            sys.exit(2)
    base_url = f"http://{lan_ip}:{port}"
    os.environ.update(
        META_SEED_DEMO="1",
        DATABASE_URL="sqlite:///./diploma.db",
        BASE_URL=base_url,
        CORS_ALLOWED_ORIGINS=f"http://localhost:{port},http://127.0.0.1:{port},{base_url}",
        UPLOADS_DIR=str(root / "uploads"),
    )
    import uvicorn

    # По умолчанию только этот компьютер; META_LAN=1 открывает сайт для телефона (QR) в локальной сети.
    host = "0.0.0.0" if os.environ.get("META_LAN") == "1" else "127.0.0.1"
    uvicorn.run("main:app", host=host, port=port)


if __name__ == "__main__":
    main()
