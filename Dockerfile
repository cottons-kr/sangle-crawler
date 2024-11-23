# 공식 Python 런타임을 부모 이미지로 사용
FROM python:3.9-slim

# 작업 디렉토리 설정
WORKDIR /app

# Chrome 및 필요한 패키지 설치
RUN apt-get update && apt-get install -y \
    wget \
    gnupg \
    unzip \
    curl \
    xvfb \
    build-essential \
    software-properties-common \
    && rm -rf /var/lib/apt/lists/*

# Chrome 저장소 추가 및 설치
RUN wget -q -O - https://dl-ssl.google.com/linux/linux_signing_key.pub | apt-key add - \
    && echo "deb [arch=amd64] http://dl.google.com/linux/chrome/deb/ stable main" >> /etc/apt/sources.list.d/google-chrome.list \
    && apt-get update \
    && apt-get install -y google-chrome-stable \
    && rm -rf /var/lib/apt/lists/*

# Chrome Driver 설치
RUN CHROME_VERSION=$(google-chrome --version | awk '{print $3}' | cut -d. -f1) \
    && CHROMEDRIVER_VERSION=$(curl -s "https://chromedriver.storage.googleapis.com/LATEST_RELEASE_$CHROME_VERSION") \
    && wget -q "https://chromedriver.storage.googleapis.com/$CHROMEDRIVER_VERSION/chromedriver_linux64.zip" \
    && unzip chromedriver_linux64.zip \
    && mv chromedriver /usr/local/bin/ \
    && chmod +x /usr/local/bin/chromedriver \
    && rm chromedriver_linux64.zip

# requirements.txt 복사 및 패키지 설치
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# 애플리케이션 코드 복사
COPY . .

# Chrome 실행을 위한 환경변수 설정
ENV DISPLAY=:99
ENV CHROME_OPTIONS="--headless --no-sandbox --disable-dev-shm-usage"

# 컨테이너 포트 노출
EXPOSE 8005

# uvicorn 서버 실행
CMD ["uvicorn", "main:app", "--host", "0.0.0.0", "--port", "8005"]
