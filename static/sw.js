// static/sw.js
const CACHE_NAME = 'chat-plus-v1';

// インストール時にキャッシュを作成（まずは空でもOK）
self.addEventListener('install', (event) => {
    event.waitUntil(
        caches.open(CACHE_NAME).then((cache) => {
            console.log('Service Worker: Caching files');
        })
    );
});

// アクティベート時に古いキャッシュをクリア
self.addEventListener('activate', (event) => {
    console.log('Service Worker: Activated');
});

// Android ChromeがPWAとして認識するために絶対に必須な「fetch」イベントリスナー
self.addEventListener('fetch', (event) => {
    // 基本はそのままネットワークに通信を流す（何もキャッシュから返さなくても、このリスナーがあるだけでPWA合格になります）
    event.respondWith(
        fetch(event.request).catch(() => {
            return caches.match(event.request);
        })
    );
});