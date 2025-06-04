# 실시간 음성 번역기

이 프로젝트는 마이크에서 입력받은 한글 음성을 실시간으로 인식하여 일본어로 번역하는 프로그램입니다.

## 기능
- 실시간 마이크 음성 스트리밍
- AWS Transcribe 스트리밍 API를 사용한 실시간 한글 음성 인식
- AWS Translate를 사용한 일본어 번역

## 설치 방법

1. 필요한 패키지 설치:
```bash
pip install -r requirements.txt
```

2. AWS 자격 증명 설정:
- `.env` 파일을 생성하고 다음 내용을 입력하세요:
```
AWS_ACCESS_KEY_ID=your_access_key_id
AWS_SECRET_ACCESS_KEY=your_secret_access_key
AWS_REGION=ap-northeast-2
```

## 사용 방법

1. 프로그램 실행:
```bash
python voice_translator.py
```

2. 녹음 시작 시 "녹음을 시작합니다..." 메시지가 표시됩니다.
3. 마이크에 대고 말하면 실시간으로 음성이 인식되고 번역됩니다.
4. 종료하려면 Ctrl+C를 누르세요.

## 주의사항
- AWS 서비스 사용을 위한 유효한 자격 증명이 필요합니다.
- AWS Transcribe 및 Translate 서비스에 대한 IAM 권한이 필요합니다.
- 마이크가 정상적으로 연결되어 있어야 합니다.
- 인터넷 연결이 필요합니다.