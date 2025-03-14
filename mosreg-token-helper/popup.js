document.addEventListener('DOMContentLoaded', function() {
    const getTokenButton = document.getElementById('getToken');
    const copyTokenButton = document.getElementById('copyToken');
    const tokenDisplay = document.getElementById('token');
    const statusDisplay = document.getElementById('status');

    // Загружаем сохраненный токен при открытии popup
    chrome.storage.local.get(['mosregToken'], function(result) {
        if (result.mosregToken) {
            tokenDisplay.textContent = result.mosregToken;
            statusDisplay.textContent = 'Токен найден';
        }
    });

    getTokenButton.addEventListener('click', function() {
        statusDisplay.textContent = 'Ожидание входа в МЭШ...';
        
        // Открываем страницу МЭШ в новой вкладке
        chrome.tabs.create({ url: 'https://authedu.mosreg.ru/' }, function(tab) {
            // Слушаем сообщения от background script
            chrome.runtime.onMessage.addListener(function(message, sender, sendResponse) {
                if (message.type === 'tokenFound') {
                    tokenDisplay.textContent = message.token;
                    statusDisplay.textContent = 'Токен успешно получен!';
                    chrome.tabs.remove(tab.id);
                }
            });
        });
    });

    copyTokenButton.addEventListener('click', function() {
        const token = tokenDisplay.textContent;
        if (token) {
            navigator.clipboard.writeText(token).then(function() {
                statusDisplay.textContent = 'Токен скопирован в буфер обмена!';
            }).catch(function() {
                statusDisplay.textContent = 'Ошибка при копировании токена';
            });
        } else {
            statusDisplay.textContent = 'Сначала получите токен';
        }
    });
}); 