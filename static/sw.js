// static/sw.js

// キャッシュ（スマホ内に保存するデータ箱）の名前。バージョン管理に使います。
const CACHE_NAME = 'chat-plus-v1';

// 1. インストールイベント（アプリがブラウザに認識された最初の1回だけ動く）
self.addEventListener('install', (event) => {
    event.waitUntil(
        // 「chat-plus-v1」という名前のキャッシュ箱を開く
        caches.open(CACHE_NAME).then((cache) => {
            // 今は空っぽのままでOKですが、箱を作ることでPWAの必須条件をクリアします
            console.log('Service Worker: Caching files');
        })
    );
});

// 2. アクティベートイベント（サービスワーカーが新しく更新された時に動く）
self.addEventListener('activate', (event) => {
    // 過去の古いバージョンのキャッシュ箱が残っていれば、ここで自動削除する命令を後々書けます
    console.log('Service Worker: Activated');
});

// 3. フェッチイベント（最重要：画面が通信をしようとする度に毎回割り込むイベント）
// ⚠️ AndroidのChromeなどでPWAとして認めてもらうために、この「fetch」の記述が【絶対条件】となっています。
self.addEventListener('fetch', (event) => {
    event.respondWith(
        // まずは普通にインターネット経由で通信（fetch）を試みる
        fetch(event.request).catch(() => {
            // もし電波が悪くて通信に失敗（catch）したら、スマホ内に保存してあるキャッシュからデータを返す
            return caches.match(event.request);
        })
    );
});