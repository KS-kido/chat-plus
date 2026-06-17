// キャッシュ名を定義（バージョン管理用）
const CACHE_NAME = 'chat-plus-v1';

// 1. インストール時：古いキャッシュがあれば削除し、即座に新しいSWを有効にする
self.addEventListener('install', (event) => {
    self.skipWaiting();
});

// 2. アクティベート時：古いキャッシュを完全に破棄してクリーンアップ
self.addEventListener('activate', (event) => {
    event.waitUntil(caches.delete(CACHE_NAME));
});

// 3. 通信時：Renderなどの外部への「古いパス」を一切呼ばない設定
// ネットワークへの通信を素直に行い、失敗した時だけキャッシュを確認する
self.addEventListener('fetch', (event) => {
    event.respondWith(
        fetch(event.request).catch(() => {
            return caches.match(event.request);
        })
    );
});