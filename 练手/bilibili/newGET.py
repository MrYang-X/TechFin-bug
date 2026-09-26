from http.client import responses
from bs4 import BeautifulSoup

import requests

head = { "User-Agent": "Mozilla/5.0 (WIndows NT 10.0; Win64; x64)" }   # 伪装用户请求头
response = requests.get("https://www.bilibili.com/", headers=head) # 传入自定义的请求头
if response.ok:
  print(response.text)
else:
  print(response)
  print("请求失败")