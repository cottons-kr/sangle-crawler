# 공식 Python 런타임을 부모 이미지로 사용
FROM python:3.9-slim

# 작업 디렉토리 설정
WORKDIR /app

# 시스템 패키지 및 Rust 설치
RUN apt-get update && apt-get install -y \
    build-essential \
    curl \
    software-properties-common \
    pkg-config \
    libssl-dev \
    && rm -rf /var/lib/apt/lists/*

# Rust 설치
RUN curl --proto '=https' --tlsv1.2 -sSf https://sh.rustup.rs | sh -s -- -y
ENV PATH="/root/.cargo/bin:${PATH}"

# 파이썬 패키지 설치를 위한 requirements.txt 복사
COPY requirements.txt .

# requirements.txt에 명시된 필요한 패키지 설치
RUN pip install --no-cache-dir -r requirements.txt

# 애플리케이션 코드 복사
COPY . .

# 컨테이너 포트 노출
EXPOSE 8005

# uvicorn 서버 실행
CMD ["uvicorn", "main:app", "--host", "0.0.0.0", "--port", "8005"]
