import zipfile
import os

# 압축파일 경로
zip_path = "project.zip"   # BLIND_APP 폴더 안에 있다면 그대로 사용

# 압축을 풀 경로
extract_dir = "./"         # 현재 폴더에 풀기

print("🔄 압축 해제 중...")

with zipfile.ZipFile(zip_path, 'r') as zip_ref:
    zip_ref.extractall(extract_dir)

print("✅ 압축 해제 완료!")
