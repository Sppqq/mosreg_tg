// Слушаем сетевые запросы
chrome.webRequest.onBeforeSendHeaders.addListener(
    function(details) {
        // Ищем заголовок Authorization в запросах к МЭШ
        const headers = details.requestHeaders;
        const authHeader = headers.find(header => header.name.toLowerCase() === 'authorization');
        
        if (authHeader && authHeader.value.startsWith('Bearer ')) {
            const token = authHeader.value.split(' ')[1];
            
            // Сохраняем токен
            chrome.storage.local.set({ mosregToken: token }, function() {
                // Отправляем сообщение в popup
                chrome.runtime.sendMessage({
                    type: 'tokenFound',
                    token: token
                });
            });
        }
    },
    { urls: ["https://authedu.mosreg.ru/*"] },
    ["requestHeaders"]
); 