import json
import time
import datetime
import threading
from websocket import WebSocketApp

class WebSocketClient:
    def __init__(self, websocket_url):
        """
        WebSocket 클라이언트 초기화
        """
        self.websocket_url = websocket_url
        self.ws = None
        self.connected = False
        self.ws_thread = None

    def connect(self):
        """
        WebSocket 연결 수립
        """
        print(f"연결 URL: {self.websocket_url}")

        # WebSocket 콜백 함수 정의
        def on_message(ws, message):
            print(f"메시지 수신: {message}")
            try:
                data = json.loads(message)
                print(f"발신자: {data.get('sender')}, 메시지: {data.get('message')}")
            except json.JSONDecodeError:
                print("JSON이 아닌 메시지 수신:", message)

        def on_error(ws, error):
            print(f"에러 발생: {error}")

        def on_close(ws, close_status_code, close_msg):
            print(f"연결 종료: {close_status_code} - {close_msg}")
            self.connected = False

        def on_open(ws):
            print("WebSocket 연결 성공!")
            self.connected = True
            # 연결 성공 시 초기 메시지 전송
            initial_message = {
                'action': 'connect',
                'message': 'Client connected'
            }
            ws.send(json.dumps(initial_message))

        # WebSocket 연결
        self.ws = WebSocketApp(
            self.websocket_url,
            on_open=on_open,
            on_message=on_message,
            on_error=on_error,
            on_close=on_close
        )

        # 백그라운드 스레드에서 WebSocket 실행
        self.ws_thread = threading.Thread(target=self.ws.run_forever)
        self.ws_thread.daemon = True
        self.ws_thread.start()

        # 연결 대기
        timeout = 5
        start_time = time.time()
        while not self.connected and time.time() - start_time < timeout:
            time.sleep(0.1)

        if not self.connected:
            raise Exception("WebSocket 연결 실패")

    def send_message(self, sender, message, translation=None):
        """
        메시지 전송
        """
        if not self.connected:
            raise Exception("WebSocket이 연결되어 있지 않습니다.")

        messageData = {
            'action': 'sendMessage',
            'sender': sender,
            'message': message,
            'translation': translation
        }
        self.ws.send(json.dumps(messageData))

    def close(self):
        """
        WebSocket 연결 종료
        """
        if self.ws:
            self.ws.close()
        if self.ws_thread:
            self.ws_thread.join(timeout=1)

def test_websocket_chat():
    """
    WebSocket 채팅 테스트
    """
    # WebSocket URL 입력
    websocket_url = input("WebSocket URL을 입력하세요: ")
    
    # 발신자 이름 입력
    sender = input("발신자 이름을 입력하세요: ")

    # 클라이언트 생성 및 연결
    client = WebSocketClient(websocket_url=websocket_url)

    try:
        # WebSocket 연결
        client.connect()

        # 메시지 전송 테스트
        while True:
            message = input("전송할 메시지를 입력하세요 (종료하려면 'quit' 입력): ")
            if message.lower() == 'quit':
                break
            translation = input("번역된 텍스트를 입력하세요: ")
            client.send_message(sender, message, translation)

    except Exception as e:
        print(f"에러 발생: {e}")
    finally:
        client.close()

if __name__ == "__main__":
    test_websocket_chat()
