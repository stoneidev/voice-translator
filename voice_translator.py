import os
import pyaudio
import boto3
import json
import asyncio
from dotenv import load_dotenv
import time
import threading
import queue
from collections import deque
import openai
from amazon_transcribe.client import TranscribeStreamingClient
from amazon_transcribe.handlers import TranscriptResultStreamHandler
from amazon_transcribe.model import TranscriptEvent
from websocket import WebSocketApp
import re

# .env 파일에서 환경 변수 로드
load_dotenv()

# OpenAI API 설정
openai.api_key = os.getenv('OPENAI_API_KEY')

class Config:
    def __init__(self):
        self.CONTEXT_SIZE = 10
        self.CHUNK = 1024
        self.FORMAT = pyaudio.paInt16
        self.CHANNELS = 1
        self.RATE = 16000
        self.SILENCE_THRESHOLD = 0.05
        self.SILENCE_DURATION = 0.5

class SentenceManager:
    def __init__(self, config):
        self.context = deque(maxlen=config.CONTEXT_SIZE)
        self.correction_queue = queue.Queue(maxsize=100)
        self.translation_queue = queue.Queue(maxsize=100)
        self.last_sentence_time = time.time()
        self.min_sentence_interval = 0.1
        self.accumulated_text = ""  # 누적된 텍스트 저장
        self.last_text_time = time.time()  # 마지막 텍스트 수신 시간
        self.max_wait_time = 4.0  # 최대 대기 시간 (초)
        self.completed_sentences = deque(maxlen=5)  # 완성된 문장 히스토리
        
    def add_text(self, text):
        """새로운 텍스트를 컨텍스트에 추가하고 문장 완성도를 확인합니다."""
        self.context.append(text)
        self.accumulated_text += " " + text if self.accumulated_text else text
        self.last_text_time = time.time()
        
        # OpenAI API를 사용한 문장 완성 확인 (타임아웃 포함)
        return self.check_sentence_completion()
        
    def check_sentence_completion_simple(self):
        """정규표현식 기반으로 문장 완성도를 더 정교하게 확인합니다."""
        if not self.accumulated_text:
            return False, ""

        text = self.accumulated_text.strip()
        
        # 물음표로 끝나는 경우 즉시 완성된 문장으로 처리
        if text.endswith("?"):
            complete_sentence = text
            self.completed_sentences.append(complete_sentence)
            self.accumulated_text = ""
            self.context.clear()
            return True, complete_sentence

        # 다양한 종결 어미 및 특수문자 패턴 (의문형 어미 추가)
        sentence_ending_pattern = re.compile(
            r"(다|요|까|죠|네|습니다|합니다|됩니다|입니다|군요|네요|랍니다|라요|구나|구요|"
            r"겠네|겠군요|겠어요|겠습니까|십시오|세요|자|죠|라|렴|구려|구요|지요|"
            r"ㄹ까|을까|나요|ㄴ가요|ㄴ가|는가|던가)"  # 의문형 어미 추가
            r"[\s\.!?\)\]\}\"\u2018\u2019\u201c\u201d]*$"  # 유니코드로 따옴표 표현
        )

        if sentence_ending_pattern.search(text):
            complete_sentence = text
            self.completed_sentences.append(complete_sentence)
            self.accumulated_text = ""
            self.context.clear()
            return True, complete_sentence

        # 최대 대기 시간 초과 시 강제로 문장 완성 처리 (글자 수 제한 완화)
        if time.time() - self.last_text_time > self.max_wait_time and len(text) > 2:  # 10 -> 2로 완화
            complete_sentence = text
            self.completed_sentences.append(complete_sentence)
            self.accumulated_text = ""
            self.context.clear()
            return True, complete_sentence

        return False, ""
        
    def check_sentence_completion(self):
        """OpenAI API를 사용하여 현재 컨텍스트가 완전한 문장인지 확인하고 정제합니다."""
        if not self.accumulated_text:
            return False, ""
            
        # 누적된 텍스트 사용
        combined_text = self.accumulated_text.strip()
        
        # 텍스트가 너무 짧으면 건너뛰기
        if len(combined_text) < 5:
            return False, ""
        
        # 이전 컨텍스트 준비 (최근 5개까지만 사용)
        recent_context = list(self.context)[-5:] if len(self.context) > 5 else list(self.context)
        context_text = " ".join(recent_context) if recent_context else ""
        
        # 이전 완성된 문장들도 컨텍스트에 포함
        previous_sentences = " ".join(list(self.completed_sentences)[-3:]) if self.completed_sentences else ""
        
        try:
            # OpenAI API 호출 (타임아웃 설정)
            response = openai.chat.completions.create(
                model="gpt-4.1-mini",
                messages=[
                    {
                        "role": "system",
                        "content": """다음 한국어 텍스트를 분석하여 완전한 문장인지 확인하고, 
                        완전한 문장이면 문장을 정제하여 반환해주세요.
                        이전 컨텍스트를 참고하여 문맥을 이해하고 판단해주세요.
                        가능한 번역이 잘될 문장 형태로 만들어주어야 해요.
                        응답은 다음 JSON 형식으로 해주세요:
                        {
                            "is_complete": true/false,
                            "sentence": "완전한 문장이면 정제된 문장, 아니면 빈 문자열"
                        }"""
                    },
                    {
                        "role": "user",
                        "content": f"이전 완성된 문장들: {previous_sentences}\n\n최근 컨텍스트: {context_text}\n\n현재 텍스트: {combined_text}"
                    }
                ],
                temperature=0.1,
                timeout=2  # 2초 타임아웃 설정
            )
            
            # 응답 파싱
            result = json.loads(response.choices[0].message.content)
            
            if result.get('is_complete', False):
                sentence = result.get('sentence', combined_text)
                # 완성된 문장을 히스토리에 저장
                self.completed_sentences.append(sentence)
                # 컨텍스트 초기화
                self.context.clear()
                self.accumulated_text = ""  # 누적 텍스트도 초기화
                return True, sentence
            
            return False, ""
            
        except Exception as e:
            print(f"OpenAI API 오류 또는 타임아웃: {str(e)}")
            # API 실패 시 간단한 규칙 기반 방식으로 대체
            return self.check_sentence_completion_simple()

class TranscriptHandler(TranscriptResultStreamHandler):
    def __init__(self, translator, transcript_result_stream, config):
        super().__init__(transcript_result_stream)
        self.translator = translator
        self.config = config
        self.sentence_manager = SentenceManager(config)
        self.partial_results = []  # 부분 결과 저장
        
    async def handle_transcript_event(self, transcript_event: TranscriptEvent):
        results = transcript_event.transcript.results
        if len(results) > 0:
            transcript = results[0]
            if transcript.is_partial:
                # 부분 결과 처리
                self.partial_results.append(transcript.alternatives[0].transcript)
            else:
                # 최종 결과 처리
                text = transcript.alternatives[0].transcript
                if text and self._is_valid_sentence(text):
                    print(f"인식된 텍스트: {text}")
                    self.sentence_manager.correction_queue.put(text)
                self.partial_results = []  # 부분 결과 초기화
                
    def _is_valid_sentence(self, text):
        # 문장 유효성 검사
        current_time = time.time()
        if current_time - self.sentence_manager.last_sentence_time < self.sentence_manager.min_sentence_interval:
            return False
        self.sentence_manager.last_sentence_time = current_time
        return True

class WebSocketClient:
    def __init__(self, websocket_url):
        self.websocket_url = websocket_url
        self.ws = None
        self.connected = False
        self.ws_thread = None
        self.message_queue = queue.Queue()

    def connect(self):
        def on_message(ws, message):
            print(f"서버로부터 메시지 수신: {message}")

        def on_error(ws, error):
            print(f"WebSocket 에러: {error}")

        def on_close(ws, close_status_code, close_msg):
            print(f"WebSocket 연결 종료: {close_status_code} - {close_msg}")
            self.connected = False

        def on_open(ws):
            print("WebSocket 연결 성공!")
            self.connected = True

        self.ws = WebSocketApp(
            self.websocket_url,
            on_open=on_open,
            on_message=on_message,
            on_error=on_error,
            on_close=on_close
        )

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

    def send_message(self, sender, message, translation):
        if not self.connected:
            raise Exception("WebSocket이 연결되어 있지 않습니다.")

        message_data = {
            "action": "sendMessage",
            "sender": sender,
            "message": {
                "original": message,
                "translation": translation
            }
        }
        self.ws.send(json.dumps(message_data))

    def close(self):
        if self.ws:
            self.ws.close()
        if self.ws_thread:
            self.ws_thread.join(timeout=1)

class VoiceTranslator:
    def __init__(self):
        start_time = time.time()
        print(f"초기화 시작 시간: {time.strftime('%H:%M:%S')}")
        
        # 설정 로드
        self.config = Config()
        print(f"설정 로드 완료: {time.time() - start_time:.2f}초")
        
        # 마이크 선택
        self.selected_mic_index = self.select_microphone()
        print(f"마이크 선택 완료: {time.time() - start_time:.2f}초")
        
        # AWS 자격 증명 설정
        self.region = os.getenv('AWS_REGION', 'ap-northeast-2')
        self.translate_client = boto3.client('translate',
            aws_access_key_id=os.getenv('AWS_ACCESS_KEY_ID'),
            aws_secret_access_key=os.getenv('AWS_SECRET_ACCESS_KEY'),
            region_name=self.region
        )
        print(f"AWS 클라이언트 초기화 완료: {time.time() - start_time:.2f}초")
        
        # Transcribe 스트리밍 클라이언트 설정
        self.client = TranscribeStreamingClient(region=self.region)
        print(f"Transcribe 클라이언트 초기화 완료: {time.time() - start_time:.2f}초")
        
        # WebSocket 클라이언트 설정
        websocket_url = os.getenv('WEBSOCKET_URL')
        if not websocket_url:
            raise ValueError("WEBSOCKET_URL 환경 변수가 설정되지 않았습니다.")
        self.ws_client = WebSocketClient(websocket_url)
        self.ws_client.connect()
        print(f"WebSocket 연결 완료: {time.time() - start_time:.2f}초")
        
        # 스레드 제어
        self.running = True
        
        # 스레드 초기화
        self.correction_thread = None
        self.translation_thread = None
        self.message_thread = None
        
        print(f"전체 초기화 완료: {time.time() - start_time:.2f}초")
        
    def select_microphone(self):
        """사용 가능한 마이크를 나열하고 사용자가 선택하도록 합니다."""
        p = pyaudio.PyAudio()
        
        # 사용 가능한 마이크 정보 수집
        mic_info = []
        for i in range(p.get_device_count()):
            device_info = p.get_device_info_by_index(i)
            if device_info.get('maxInputChannels') > 0:  # 입력 장치만 표시
                mic_info.append({
                    'index': i,
                    'name': device_info.get('name', 'Unknown'),
                    'channels': device_info.get('maxInputChannels'),
                    'sample_rate': device_info.get('defaultSampleRate')
                })
        
        if not mic_info:
            print("사용 가능한 마이크가 없습니다.")
            p.terminate()
            return None
            
        # 마이크 목록 출력
        print("\n사용 가능한 마이크 목록:")
        for i, mic in enumerate(mic_info):
            print(f"{i+1}. {mic['name']} (채널: {mic['channels']}, 샘플레이트: {mic['sample_rate']}Hz)")
            
        # 사용자 선택
        while True:
            try:
                choice = int(input("\n사용할 마이크 번호를 선택하세요 (1-{}): ".format(len(mic_info))))
                if 1 <= choice <= len(mic_info):
                    selected_mic = mic_info[choice-1]
                    print(f"\n선택된 마이크: {selected_mic['name']}")
                    p.terminate()
                    return selected_mic['index']
                else:
                    print("유효하지 않은 번호입니다. 다시 선택해주세요.")
            except ValueError:
                print("숫자를 입력해주세요.")
                
    def translate_text(self, text):
        """AWS Translate를 사용하여 텍스트를 일본어로 번역합니다."""
        if not text:
            return ""
            
        try:
            response = self.translate_client.translate_text(
                Text=text,
                SourceLanguageCode='ko',
                TargetLanguageCode='ja'
            )
            return response['TranslatedText']
        except Exception as e:
            print(f"번역 오류: {str(e)}")
            return ""
    
    def correction_worker(self, sentence_manager):
        """AI 교정 작업을 처리하는 워커 스레드"""
        while self.running:
            try:
                # 큐에서 텍스트 가져오기 (0.5초 타임아웃으로 단축)
                text = sentence_manager.correction_queue.get(timeout=0.5)
                # 문장 완성도 확인
                is_complete, complete_sentence = sentence_manager.add_text(text)
                if is_complete and complete_sentence:
                    print(f"완성된 문장: {complete_sentence}")
                    # 번역을 위한 텍스트를 큐에 추가
                    sentence_manager.translation_queue.put(complete_sentence)
                sentence_manager.correction_queue.task_done()
            except queue.Empty:
                # 큐가 비어있을 때 대기 중인 텍스트 확인
                if sentence_manager.accumulated_text and \
                   time.time() - sentence_manager.last_text_time > sentence_manager.max_wait_time:
                    # 강제로 문장 완성 처리
                    is_complete, complete_sentence = sentence_manager.check_sentence_completion_simple()
                    if is_complete and complete_sentence:
                        print(f"타임아웃으로 완성된 문장: {complete_sentence}")
                        sentence_manager.translation_queue.put(complete_sentence)
                continue
            except Exception as e:
                print(f"교정 스레드 오류: {str(e)}")
                continue
    
    def translation_worker(self, sentence_manager):
        """번역 작업을 처리하는 워커 스레드"""
        while self.running:
            try:
                # 큐에서 텍스트 가져오기 (1초 타임아웃)
                text = sentence_manager.translation_queue.get(timeout=1)
                translated_text = self.translate_text(text)
                print(f"번역된 텍스트: {translated_text}")
                
                # WebSocket으로 메시지 전송
                self.ws_client.send_message("VoiceTranslator", text, translated_text)
                
                sentence_manager.translation_queue.task_done()
            except queue.Empty:
                continue
            except Exception as e:
                print(f"번역 스레드 오류: {str(e)}")
                continue
    
    async def mic_stream(self):
        """마이크에서 오디오를 스트리밍합니다."""
        # 마이크 초기화
        p = pyaudio.PyAudio()
        stream = p.open(
            format=self.config.FORMAT,
            channels=self.config.CHANNELS,
            rate=self.config.RATE,
            input=True,
            input_device_index=self.selected_mic_index,  # 선택된 마이크 사용
            frames_per_buffer=self.config.CHUNK,
        )
        
        print("녹음을 시작합니다... (종료하려면 Ctrl+C를 누르세요)")
        
        try:
            while self.running:
                chunk = stream.read(self.config.CHUNK, exception_on_overflow=False)
                yield chunk
        except KeyboardInterrupt:
            print("\n녹음을 종료합니다.")
        finally:
            stream.stop_stream()
            stream.close()
            p.terminate()
            print("마이크가 종료되었습니다.")

    async def write_chunks(self, stream):
        """오디오 청크를 스트림에 전송합니다."""
        async for chunk in self.mic_stream():
            await stream.input_stream.send_audio_event(audio_chunk=chunk)
        await stream.input_stream.end_stream()

    async def process_audio(self):
        """전체 프로세스를 실행합니다."""
        try:
            print("\n🎤 음성 인식 시스템이 준비되었습니다!")
            print("이제 말씀하시면 자동으로 인식되어 번역됩니다.")
            print("종료하시려면 Ctrl+C를 누르세요.\n")
            
            # 트랜스크립션 스트림 시작
            stream = await self.client.start_stream_transcription(
                language_code="ko-KR",
                media_sample_rate_hz=self.config.RATE,
                media_encoding="pcm",
                vocabulary_name="p2pVocabulary",  # 사용자 정의 사전 추가
                enable_partial_results_stabilization=True,  # 부분 결과 안정화 활성화
                partial_results_stability="high",  # 높은 안정성 설정
            )
            
            # 핸들러 생성 및 연결
            handler = TranscriptHandler(self, stream.output_stream, self.config)
            
            # 교정 스레드 시작
            self.correction_thread = threading.Thread(
                target=self.correction_worker,
                args=(handler.sentence_manager,),
                daemon=True
            )
            self.correction_thread.start()
            
            # 번역 스레드 시작
            self.translation_thread = threading.Thread(
                target=self.translation_worker,
                args=(handler.sentence_manager,),
                daemon=True
            )
            self.translation_thread.start()
            
            # 핸들러 연결
            await asyncio.gather(
                self.write_chunks(stream),
                handler.handle_events(),
            )
            
        except Exception as e:
            print(f"오류 발생: {str(e)}")
            self.running = False
        finally:
            # 프로그램 종료 시 정리
            self.running = False
            if self.correction_thread and self.correction_thread.is_alive():
                self.correction_thread.join(timeout=1)
            if self.translation_thread and self.translation_thread.is_alive():
                self.translation_thread.join(timeout=1)
            self.ws_client.close()

async def main():
    translator = VoiceTranslator()
    await translator.process_audio()

if __name__ == "__main__":
    asyncio.run(main()) 
