import cv2
import pytesseract
from PIL import Image
import numpy as np

# -------------------------------------------------------------
# ★★★ 初期設定 (ご自身の環境に合わせて書き換えてください) ★★★
# -------------------------------------------------------------

# 1. Tesseract-OCRの実行ファイルのパスを指定する
#    Windowsでデフォルトの場所にインストールした場合の例
pytesseract.pytesseract.tesseract_cmd = r'C:\Program Files\Tesseract-OCR\tesseract.exe'

# 2. 読み込むサンプル画像のパス
#    プロジェクトのルートフォルダ(NSS-result-tracker)から見たパスを指定
SAMPLE_IMAGE_PATH = 'sample_images/sample_lose_norank_01.png' # ← 試したい画像の名前に変更

# 3. 文字を読み取る範囲 (ROI: Region of Interest) の座標を指定する
#    (左上のX座標, 左上のY座標), (右下のX座標, 右下のY座標)
#    例: ROI_RESULT = (850, 480, 1080, 580)
ROI_RESULT = (175, 85, 510, 260)  # ← ★ご自身で調べた「WIN/LOSE」の座標に書き換えてください
ROI_SCORE = (125, 280, 610, 445)   # ← ★ご自身で調べた「スコア」の座標に書き換えてください

# -------------------------------------------------------------

def extract_text_from_image(image_path):
    """画像ファイルから指定した範囲のテキストを抽出する"""
    try:
        # 画像を読み込む
        img = cv2.imread(image_path)
        if img is None:
            print(f"エラー: 画像ファイルが見つかりません: {image_path}")
            return

        # OpenCVのBGR形式からPillowのRGB形式へ変換（pytesseractで扱うため）
        img_rgb = cv2.cvtColor(img, cv2.COLOR_BGR2RGB)
        pil_img = Image.fromarray(img_rgb)

        # 範囲を指定して画像を切り抜く
        # crop()の引数は (左, 上, 右, 下)
        result_img_crop = pil_img.crop(ROI_RESULT)
        score_img_crop = pil_img.crop(ROI_SCORE)

        # 前処理を適用
        processed_result_img = preprocess_for_ocr(result_img_crop)
        processed_score_img = preprocess_for_ocr(score_img_crop)

        # tesseractでOCRを実行 (設定を調整)
        # lang='eng': 英語として読み取る
        # --psm 7: 画像を1行のテキストとして扱う
        result_text = pytesseract.image_to_string(processed_result_img, lang='eng', config='--psm 7').strip()
        score_text = pytesseract.image_to_string(processed_score_img, lang='eng', config='--psm 7').strip()

        # 結果を表示
        print(f"--- 読み取り結果 ---")
        print(f"勝敗: {result_text}")
        print(f"スコア: {score_text}")
        print(f"--------------------")

    except Exception as e:
        print(f"エラーが発生しました: {e}")

# OCRのための画像前処理を行う関数"
def preprocess_for_ocr(img_crop):
    # PillowイメージからOpenCVイメージへ変換
    img_np = np.array(img_crop)

    # 1. グレースケール化
    gray_img = cv2.cvtColor(img_np, cv2.COLOR_RGB2GRAY)

    # 2. 二値化（文字をくっきりさせる）
    #    閾値(180)より明るいピクセルは白(255)に、それ以外は黒(0)にする
    #    この「180」という値は、画像の明るさによって調整が必要な場合があります
    _, binary_img = cv2.threshold(gray_img, 180, 255, cv2.THRESH_BINARY)

    # ★デバッグ用：処理後の画像を保存して確認したい場合は以下のコメントを外す
    # cv2.imwrite('debug_image.png', binary_img)

    return binary_img

# メイン処理
if __name__ == '__main__':
    extract_text_from_image(SAMPLE_IMAGE_PATH)