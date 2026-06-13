import socket, threading, logging, time, signal, sys

logger = logging.getLogger("frog.proxy")

DEFAULT_BIND = "100.102.135.43"
DEFAULT_PORT = 8080
DEFAULT_TARGET = "100.92.101.49"
DEFAULT_TARGET_PORT = 8976


def _forward(src, dst, label):
    try:
        while True:
            data = src.recv(65536)
            if not data:
                break
            dst.sendall(data)
    except (ConnectionResetError, BrokenPipeError, OSError):
        pass


def start(bind_addr: str = DEFAULT_BIND, bind_port: int = DEFAULT_PORT,
          target_host: str = DEFAULT_TARGET, target_port: int = DEFAULT_TARGET_PORT,
          shutdown_event: threading.Event = None):

    s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    s.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    try:
        s.bind((bind_addr, bind_port))
    except OSError as e:
        logger.error("Cannot bind %s:%d: %s", bind_addr, bind_port, e)
        return
    s.listen(10)
    s.settimeout(1.0)
    logger.info("Proxy listening %s:%d -> %s:%d", bind_addr, bind_port, target_host, target_port)

    running = True
    while running:
        if shutdown_event and shutdown_event.is_set():
            break
        try:
            conn, addr = s.accept()
            logger.debug("Connection from %s", addr)
            t = threading.Thread(target=_handle, args=(conn, target_host, target_port), daemon=True)
            t.start()
        except socket.timeout:
            continue
        except OSError:
            break
    s.close()


def _handle(conn, target_host, target_port):
    try:
        remote = socket.create_connection((target_host, target_port), timeout=10)
    except (socket.timeout, ConnectionRefusedError, OSError) as e:
        logger.warning("Cannot connect to %s:%d: %s", target_host, target_port, e)
        conn.close()
        return
    t1 = threading.Thread(target=_forward, args=(conn, remote, "c2r"), daemon=True)
    t2 = threading.Thread(target=_forward, args=(remote, conn, "r2c"), daemon=True)
    t1.start()
    t2.start()
    t1.join()
    t2.join()
    conn.close()
    remote.close()


def serve_forever(bind_addr=DEFAULT_BIND, bind_port=DEFAULT_PORT,
                  target_host=DEFAULT_TARGET, target_port=DEFAULT_TARGET_PORT):
    stop = threading.Event()

    def _signal(signum, frame):
        logger.info("Shutting down proxy (signal %d)", signum)
        stop.set()

    signal.signal(signal.SIGTERM, _signal)
    signal.signal(signal.SIGINT, _signal)

    start(bind_addr, bind_port, target_host, target_port, stop)


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO,
                        format="%(asctime)s [%(name)s] %(levelname)s: %(message)s")
    serve_forever()
