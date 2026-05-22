// アプリ化を有効にするための最小構成サービスワーカー
self.addEventListener('install', (e) => {
    console.log('Service Worker: Installed');
});

self.addEventListener('fetch', (e) => {
    // リアルタイム通信（Socket.IO）を邪魔しないよう、すべてスルー（ネットワーク優先）にする
    return;
});