from bs4 import BeautifulSoup

import requests

head = { "User-Agent": "Mozilla/5.0 (WIndows NT 10.0; Win64; x64)" }
response = requests.get("https://www.bilibili.com/", headers=head)

html = response.text
soup = BeautifulSoup(html, "html.parser")
all_titles = soup.findAll("div", attrs={"class": "feed-card"})
for title in all_titles:
  all_links = title.findAll("a")
  for link in all_links:
    print(link.string)