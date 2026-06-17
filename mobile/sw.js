/**
 * sw.js — Service Worker for Last Man Standing mobile companion app.
 * Handles background push event captures and formats incoming system notifications.
 */

// Import Firebase App and Messaging in the Service Worker context
importScripts('https://www.gstatic.com/firebasejs/9.22.0/firebase-app-compat.js');
importScripts('https://www.gstatic.com/firebasejs/9.22.0/firebase-messaging-compat.js');

// Config object matching app.js
const firebaseConfig = {
  apiKey: "YOUR_API_KEY",
  authDomain: "YOUR_PROJECT_ID.firebaseapp.com",
  projectId: "YOUR_PROJECT_ID",
  storageBucket: "YOUR_PROJECT_ID.appspot.com",
  messagingSenderId: "YOUR_MESSAGING_SENDER_ID",
  appId: "YOUR_APP_ID"
};

try {
  firebase.initializeApp(firebaseConfig);
  const messaging = firebase.messaging();

  // Background message handler
  messaging.onBackgroundMessage((payload) => {
    console.log('Background message received: ', payload);
    const notificationTitle = payload.notification.title || "LMS Tournament Warning";
    const notificationOptions = {
      body: payload.notification.body || "A game event has occurred.",
      icon: 'icon-192.png',
      badge: 'icon-192.png',
      tag: 'lms-boss-event',
      vibrate: [300, 100, 300, 100, 400],
      data: payload.data
    };

    self.registration.showNotification(notificationTitle, notificationOptions);
  });
} catch (e) {
  console.log("Firebase background messaging not initialized or skipped (local mock setup):", e);
}

// Fallback direct Push event listener in case raw WebPush payload format is forwarded
self.addEventListener('push', (event) => {
  if (event.data) {
    try {
      const payload = event.data.json();
      const title = payload.notification?.title || "LAST MAN STANDING";
      const options = {
        body: payload.notification?.body || payload.data?.message || "Tournament Boss Event Alert!",
        icon: 'icon-192.png',
        badge: 'icon-192.png',
        vibrate: [200, 100, 200],
        data: payload.data
      };
      event.waitUntil(self.registration.showNotification(title, options));
    } catch (e) {
      // Handle plain text payloads
      const text = event.data.text();
      const options = {
        body: text,
        icon: 'icon-192.png',
        vibrate: [200, 100, 200]
      };
      event.waitUntil(self.registration.showNotification("LAST MAN STANDING ALARM", options));
    }
  }
});

// Deep link redirect when user taps notification
self.addEventListener('notificationclick', (event) => {
  event.notification.close();
  
  // Define destination path (could deep link to open client or local dashboard)
  const targetUrl = self.location.origin;
  
  event.waitUntil(
    clients.matchAll({ type: 'window', includeUncontrolled: true }).then((clientList) => {
      for (const client of clientList) {
        if (client.url === targetUrl && 'focus' in client) {
          return client.focus();
        }
      }
      if (clients.openWindow) {
        return clients.openWindow(targetUrl);
      }
    })
  );
});
