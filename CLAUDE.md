# 勤源青崧居 — LINE AI 客服機器人

## 客戶資訊
- 品牌：勤源青崧居
- 產業：房地產（桃園龍潭建案）
- 聯絡人：林小姐 0980-460395
- 老闆 LINE User ID: U139541a789ae55afa3c3d551e966d6fb

## 架構
- 單檔 Flask 應用（app.py），部署在 Railway
- AI 模型：claude-sonnet-4-20250514
- 設定儲存：JSON 檔案掛載在 Railway Volume（/data/settings.json）
- SYSTEM_PROMPT 和 TRIGGER_WORDS 可在後台「設定」頁面即時修改

## 部署資訊
- 部署網址：https://line-ai-qingyuan-production.up.railway.app
- 後台網址：https://line-ai-qingyuan-production.up.railway.app/admin
- GitHub: chieh1580/line-ai-qingyuan
- Railway Project ID: b9794ecb-bee7-4e2b-9b07-3189f0bfbf64
- 已連結 GitHub，push 後自動部署

## 環境變數（在 Railway 設定）
- CLAUDE_API_KEY
- LINE_TOKEN
- ADMIN_PASSWORD
- ADMIN_URL

## 注意事項
- 所有回覆使用繁體中文
- 修改程式碼後 push 到 GitHub 即自動部署
