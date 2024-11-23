import time
import logging
import re
import threading
import asyncio
from typing import List, Optional, Dict, Any
from datetime import datetime
from concurrent.futures import ThreadPoolExecutor
from contextlib import asynccontextmanager

from selenium import webdriver
from selenium.webdriver.chrome.service import Service
from selenium.webdriver.chrome.options import Options
from selenium.webdriver.common.by import By
from selenium.webdriver.support.ui import WebDriverWait
from selenium.webdriver.support import expected_conditions as EC
from selenium.common.exceptions import TimeoutException, NoSuchElementException

from fastapi import FastAPI, HTTPException
from pydantic import BaseModel
import uvicorn
from webdriver_manager.chrome import ChromeDriverManager

class ScraperConfig:
    WAIT_TIME = 10
    LOAD_WAIT = 2
    MAX_WORKERS = 3
    PORT = 8005

    URLS = {
        'koreannet': 'https://www.koreannet.or.kr/front/allproduct/prodSrchList.do',
        'food_safety': 'https://www.foodsafetykorea.go.kr/portal/specialinfo/searchInfoProduct.do?menu_grp=MENU_NEW04&menu_no=2815'
    }

    XPATHS = {
        'koreannet': {
            'product_link': '//*[@id="listForm"]/div/div/ul/li/div/div[2]/div/a/div[2]',
            'product_name': '//*[@id="listForm"]/div/div/ul/li/div/div[2]/div/a/div[2]',
            'manufacturer': '//*[@id="listForm"]/div/div/ul/li/div/div[2]/div/div',
            'image': '//*[@id="listForm"]/div/div/ul/li/div/div[1]/a/img',
            'report_number': '/html/body/div[2]/form/div/div/div[3]/div[2]/div[4]/div[4]/div[2]'
        },
        'food_safety': {
            'search_box': '//*[@id="prdlst_report_no1"]',
            'search_button': '//*[@id="srchBtn"]',
            'loading': '/html/body/div[1]',
            'expiry_info': '//*[@id="tbody"]/tr/td[5]/span[2]'
        }
    }

class RetryHandler:
    def __init__(self, max_retries: int = 3, delay: float = 1.0):
        self.max_retries = max_retries
        self.delay = delay
        self.logger = logging.getLogger(__name__)

    async def retry_async(self, func, *args, **kwargs):
        for attempt in range(self.max_retries):
            try:
                return await func(*args, **kwargs)
            except Exception as e:
                if attempt == self.max_retries - 1:
                    raise
                self.logger.warning(f"Attempt {attempt + 1} failed: {str(e)}")
                await asyncio.sleep(self.delay * (attempt + 1))

    def retry_sync(self, func, *args, **kwargs):
        for attempt in range(self.max_retries):
            try:
                return func(*args, **kwargs)
            except Exception as e:
                if attempt == self.max_retries - 1:
                    raise
                self.logger.warning(f"Attempt {attempt + 1} failed: {str(e)}")
                time.sleep(self.delay * (attempt + 1))

class BarcodeInfoScraper:
    def __init__(self):
        self.config = ScraperConfig()
        self.retry_handler = RetryHandler()
        self.setup_logging()
        self.logger.info("BarcodeInfoScraper 초기화 시작")
        self.driver = self.setup_driver()
        self.cache = {}
        self.cache_lock = threading.Lock()
        self.logger.info("BarcodeInfoScraper 초기화 완료")

    def setup_logging(self):
        logging.basicConfig(
            level=logging.INFO,
            format='%(asctime)s - %(name)s - %(levelname)s - [%(filename)s:%(lineno)d] - %(message)s',
            handlers=[
                logging.FileHandler('barcode_scraper.log'),
                logging.StreamHandler()
            ]
        )
        self.logger = logging.getLogger(__name__)

    def setup_driver(self) -> webdriver.Chrome:
        self.logger.info("Chrome 드라이버 자동 설치 및 설정 시작")
        try:
            # Chrome 드라이버 자동 설치
            chrome_service = Service(ChromeDriverManager().install())

            # Chrome 옵션 설정
            chrome_options = Options()
            chrome_options.add_argument('--headless=new')
            chrome_options.add_argument('--disable-gpu')
            chrome_options.add_argument('--no-sandbox')
            chrome_options.add_argument('--disable-dev-shm-usage')
            chrome_options.add_argument('--window-size=1920,1080')

            # Chrome 드라이버 생성
            driver = webdriver.Chrome(service=chrome_service, options=chrome_options)

            self.logger.info("Chrome 드라이버 생성 성공")
            return driver

        except Exception as e:
            self.logger.error(f"Chrome 드라이버 생성 실패: {str(e)}", exc_info=True)
            raise

    def find_element_safely(self, by: By, value: str, timeout: int = None) -> Optional[Any]:
        timeout = timeout or self.config.WAIT_TIME
        try:
            element = WebDriverWait(self.driver, timeout).until(
                EC.presence_of_element_located((by, value))
            )
            return element
        except (TimeoutException, NoSuchElementException):
            return None

    def extract_report_numbers(self, text: str) -> List[str]:
        self.logger.debug(f"품목보고번호 추출 시작 - 원본 텍스트: {text}")
        numbers = re.findall(r'\d{8,}', text)
        self.logger.debug(f"추출된 품목보고번호: {numbers}")
        return numbers

    def get_food_safety_info(self, report_numbers: List[str]) -> Dict[str, Any]:
        self.logger.info(f"식품안전나라 정보 조회 시작 - 품목보고번호: {report_numbers}")

        if isinstance(report_numbers, str):
            report_numbers = [report_numbers]

        results = []
        for report_number in report_numbers:
            try:
                result = self.retry_handler.retry_sync(
                    self._process_single_report_number,
                    report_number
                )
                results.append(result)
            except Exception as e:
                self.logger.error(f"품목보고번호 {report_number} 처리 실패: {str(e)}")
                results.append(f"조회 실패 ({str(e)})")

        if results:
            return {
                'success': True,
                'expiry_info': ' | '.join(filter(None, results))
            }
        return {
            'success': False,
            'message': '모든 품목보고번호 조회 실패'
        }

    def _process_single_report_number(self, report_number: str) -> str:
        self.driver.get(self.config.URLS['food_safety'])

        search_box = self.find_element_safely(
            By.XPATH,
            self.config.XPATHS['food_safety']['search_box']
        )
        if not search_box:
            raise Exception("검색창을 찾을 수 없음")

        search_box.clear()
        search_box.send_keys(report_number)

        search_button = self.find_element_safely(
            By.XPATH,
            self.config.XPATHS['food_safety']['search_button']
        )
        if search_button:
            search_button.click()

        # 로딩 대기 처리
        try:
            loading = self.find_element_safely(
                By.XPATH,
                self.config.XPATHS['food_safety']['loading'],
                timeout=3
            )
            if loading:
                WebDriverWait(self.driver, 10).until(
                    EC.invisibility_of_element_located((
                        By.XPATH,
                        self.config.XPATHS['food_safety']['loading']
                    ))
                )
        except TimeoutException:
            self.logger.debug("로딩 화면이 감지되지 않음")

        expiry_info = self.find_element_safely(
            By.XPATH,
            self.config.XPATHS['food_safety']['expiry_info']
        )

        if expiry_info:
            info_text = expiry_info.text.strip()
            if '(' in report_number:
                factory = re.search(r'\((.*?)\)', report_number).group(1)
                return f"{factory}: {info_text}"
            return info_text

        if '(' in report_number:
            factory = re.search(r'\((.*?)\)', report_number).group(1)
            return f"{factory}: 정보 없음"
        return "정보 없음"

    def get_product_info(self, barcode: str) -> Dict[str, Any]:
        self.logger.info(f"바코드 {barcode} 정보 조회 프로세스 시작")
        start_time = time.time()

        with self.cache_lock:
            if barcode in self.cache:
                self.logger.info(f"바코드 {barcode} 캐시에서 조회 성공")
                return self.cache[barcode]

        try:
            result = self.retry_handler.retry_sync(
                self._process_single_barcode,
                barcode
            )

            end_time = time.time()
            self.logger.info(
                f"바코드 {barcode} 정보 조회 완료 "
                f"(소요시간: {end_time - start_time:.2f}초)"
            )

            with self.cache_lock:
                self.cache[barcode] = result

            return result

        except Exception as e:
            self.logger.error(f"바코드 {barcode} 정보 조회 중 예외 발생", exc_info=True)
            return {
                'barcode': barcode,
                'success': False,
                'message': f'오류 발생: {str(e)}'
            }

    def _process_single_barcode(self, barcode: str) -> Dict[str, Any]:
        self.driver.get(self.config.URLS['koreannet'])

        search_box = self.find_element_safely(By.ID, 'searchText')
        if not search_box:
            raise Exception("검색창을 찾을 수 없음")

        search_box.clear()
        search_box.send_keys(barcode)

        search_button = self.find_element_safely(By.CLASS_NAME, 'submit')
        if search_button:
            search_button.click()

        time.sleep(self.config.LOAD_WAIT)

        product_link = self.find_element_safely(
            By.XPATH,
            self.config.XPATHS['koreannet']['product_link']
        )
        if not product_link:
            return {
                'barcode': barcode,
                'success': False,
                'message': '검색 결과가 없습니다.'
            }

        product_info = self._collect_basic_product_info(barcode)

        product_link.click()
        time.sleep(self.config.LOAD_WAIT)

        report_number_element = self.find_element_safely(
            By.XPATH,
            self.config.XPATHS['koreannet']['report_number']
        )

        if report_number_element:
            report_number_text = report_number_element.text.strip()
            report_numbers = self.extract_report_numbers(report_number_text)

            safety_info = self.get_food_safety_info(report_numbers)
            expiry_info = safety_info.get('expiry_info') if safety_info['success'] else '정보 없음'
        else:
            report_number_text = "정보 없음"
            expiry_info = "정보 없음"

        product_info.update({
            '품목보고번호': report_number_text,
            '소비기한': expiry_info
        })

        return {
            'barcode': barcode,
            'success': True,
            'product_info': product_info
        }

    def _collect_basic_product_info(self, barcode: str) -> Dict[str, str]:
        product_name_element = self.find_element_safely(
            By.XPATH,
            self.config.XPATHS['koreannet']['product_name']
        )
        manufacturer_element = self.find_element_safely(
            By.XPATH,
            self.config.XPATHS['koreannet']['manufacturer']
        )
        image_element = self.find_element_safely(
            By.XPATH,
            self.config.XPATHS['koreannet']['image']
        )

        return {
            '제품명': product_name_element.text.strip() if product_name_element else "정보 없음",
            '카테고리': manufacturer_element.text.strip() if manufacturer_element else "정보 없음",
            '이미지URL': image_element.get_attribute('src') if image_element else None,
            '바코드': barcode
        }

    def close(self):
        if hasattr(self, 'driver') and self.driver:
            try:
                self.driver.quit()
                self.logger.info('Chrome 드라이버 종료 성공')
            except Exception as e:
                self.logger.error(f'Chrome 드라이버 종료 중 오류 발생: {str(e)}', exc_info=True)

class BarcodeRequest(BaseModel):
    barcodes: List[str]

class ProductInfo(BaseModel):
    품목보고번호: str
    제품명: str
    카테고리: str
    이미지URL: Optional[str]
    바코드: str
    소비기한: str

class BarcodeResponse(BaseModel):
    barcode: str
    success: bool
    product_info: Optional[ProductInfo] = None
    message: Optional[str] = None
    # scraper = None  # 이 줄을 삭제하세요

@asynccontextmanager
async def lifespan(app: FastAPI):
    global scraper
    logging.info("애플리케이션 시작: 스크래퍼 초기화")
    scraper = BarcodeInfoScraper()
    yield
    logging.info("애플리케이션 종료: 스크래퍼 정리")
    if scraper:
        scraper.close()

app = FastAPI(lifespan=lifespan)
executor = ThreadPoolExecutor(max_workers=ScraperConfig.MAX_WORKERS)

@app.post("/api/v1/barcode", response_model=List[BarcodeResponse])
async def get_barcode_info(request: BarcodeRequest):
    logger = logging.getLogger(__name__)
    logger.info(f"바코드 조회 요청 받음 - 바코드 개수: {len(request.barcodes)}")
    logger.debug(f"요청된 바코드 목록: {request.barcodes}")

    start_time = time.time()

    if not request.barcodes:
        logger.warning("빈 바코드 목록으로 요청됨")
        raise HTTPException(status_code=400, detail="바코드 목록이 비어있습니다")

    try:
        # 비동기로 여러 바코드 처리
        logger.debug(f"비동기 처리 시작 - 동시 처리 바코드 수: {len(request.barcodes)}")
        tasks = []

        for barcode in request.barcodes:
            logger.debug(f"바코드 {barcode} 처리 작업 생성")
            tasks.append(
                asyncio.get_event_loop().run_in_executor(
                    executor,
                    scraper.get_product_info,
                    barcode
                )
            )

        results = await asyncio.gather(*tasks)

        end_time = time.time()
        processing_time = end_time - start_time
        success_count = sum(1 for r in results if r['success'])

        logger.info(
            f"바코드 처리 완료 - 총 처리: {len(results)}, "
            f"성공: {success_count}, "
            f"실패: {len(results) - success_count}, "
            f"처리 시간: {processing_time:.2f}초"
        )

        for result in results:
            if result['success']:
                logger.debug(f"성공한 바코드 {result['barcode']} 처리 결과: {result['product_info']}")
            else:
                logger.warning(f"실패한 바코드 {result['barcode']} 오류 메시지: {result.get('message')}")

        return results

    except Exception as e:
        logger.error("바코드 처리 중 예외 발생", exc_info=True)
        raise HTTPException(status_code=500, detail=f"서버 오류: {str(e)}")

@app.get("/health")
async def health_check():
    logger = logging.getLogger(__name__)
    logger.debug("헬스 체크 엔드포인트 호출됨")
    try:
        response = {
            "status": "healthy",
            "timestamp": datetime.now().isoformat(),
            "scraper_status": "ready" if scraper else "not_initialized",
            "version": "1.0.0"
        }
        logger.info(f"헬스 체크 응답: {response}")
        return response
    except Exception as e:
        logger.error("헬스 체크 중 오류 발생", exc_info=True)
        raise HTTPException(status_code=500, detail=str(e))

if __name__ == "__main__":
    logging.info("서버 시작")
    try:
        uvicorn.run(
            "main:app",
            host="0.0.0.0",
            port=ScraperConfig.PORT,
            reload=False
        )
    except Exception as e:
        logging.error("서버 실행 중 오류 발생", exc_info=True)