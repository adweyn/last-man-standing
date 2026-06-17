/**
 * app.js — Mobile PWA setup, authentication, and Firebase Cloud Messaging registration.
 * Fallbacks to Mock Local Notifications if Firebase credentials are not supplied.
 */

// Firebase Configuration template
// Users will need to substitute this configuration with their own Firebase project settings.
const firebaseConfig = {
  apiKey: "YOUR_API_KEY",
  authDomain: "YOUR_PROJECT_ID.firebaseapp.com",
  projectId: "YOUR_PROJECT_ID",
  storageBucket: "YOUR_PROJECT_ID.appspot.com",
  messagingSenderId: "YOUR_MESSAGING_SENDER_ID",
  appId: "YOUR_APP_ID"
};

// Global variables
let jwtToken = localStorage.getItem("jwt_token") || null;
let apiUrl = localStorage.getItem("api_url") || "http://localhost:8000";
let username = localStorage.getItem("username") || "";

// Firebase reference
let messaging = null;

// Initialize app elements
document.addEventListener("DOMContentLoaded", () => {
  document.getElementById("serverUrl").value = apiUrl;
  
  if (jwtToken) {
    showProfile();
  }

  // Setup event listeners
  document.getElementById("loginBtn").addEventListener("click", handleLogin);
  document.getElementById("logoutBtn").addEventListener("click", handleLogout);
  document.getElementById("requestNotifBtn").addEventListener("click", requestNotificationPermission);
  
  // Register Service Worker
  registerServiceWorker();
  
  // Attempt to initialize Firebase if configured
  tryInitFirebase();
});

function registerServiceWorker() {
  if ('serviceWorker' in navigator) {
    navigator.serviceWorker.register('sw.js')
      .then(reg => {
        console.log('Service Worker registered successfully with scope:', reg.scope);
      })
      .catch(err => {
        console.error('Service Worker registration failed:', err);
      });
  }
}

function tryInitFirebase() {
  if (firebaseConfig.apiKey !== "YOUR_API_KEY") {
    try {
      firebase.initializeApp(firebaseConfig);
      messaging = firebase.messaging();
      
      // Handle foreground notifications
      messaging.onMessage((payload) => {
        console.log('Foreground message received: ', payload);
        showLocalNotification(payload.notification.title, payload.notification.body);
      });
      console.log("Firebase Messaging initialized.");
    } catch (e) {
      console.error("Firebase init failed:", e);
    }
  } else {
    console.log("Firebase config not loaded. PWA will operate in Mock Push notification mode.");
    document.getElementById("notifStatus").innerText = "FCM Config Unset (Mock Mode)";
  }
}

// ─────────────────────────────────────────────────────────────────────────────
// Auth Functions
// ─────────────────────────────────────────────────────────────────────────────

async function handleLogin() {
  const userField = document.getElementById("username").value.trim();
  const passField = document.getElementById("password").value.trim();
  apiUrl = document.getElementById("serverUrl").value.trim();

  if (!userField || !passField) {
    showAlert("Please fill in credentials", "error");
    return;
  }

  try {
    const response = await fetch(`${apiUrl}/login`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ username: userField, password: passField })
    });

    if (response.ok) {
      const data = await response.json();
      jwtToken = data.access_token;
      username = userField;

      localStorage.setItem("jwt_token", jwtToken);
      localStorage.setItem("api_url", apiUrl);
      localStorage.setItem("username", username);

      showAlert("Linked successfully!", "success");
      showProfile();
      
      // Auto register token if permission already granted
      if (Notification.permission === "granted") {
        syncPushToken();
      }
    } else {
      const err = await response.json();
      showAlert(err.detail || "Authentication failed", "error");
    }
  } catch (e) {
    showAlert(`Connection failed: ${e.message}`, "error");
  }
}

function handleLogout() {
  jwtToken = null;
  username = "";
  localStorage.removeItem("jwt_token");
  localStorage.removeItem("username");

  document.getElementById("username").value = "";
  document.getElementById("password").value = "";

  document.getElementById("profileCard").style.display = "none";
  document.getElementById("loginCard").style.display = "flex";
  document.getElementById("syncStatus").className = "status-badge disconnected";
  document.getElementById("syncStatus").innerText = "Unlinked";
}

async function showProfile() {
  document.getElementById("loginCard").style.display = "none";
  document.getElementById("profileCard").style.display = "flex";
  
  document.getElementById("profileUsername").innerText = username;
  document.getElementById("syncStatus").className = "status-badge connected";
  document.getElementById("syncStatus").innerText = "Linked";

  try {
    const response = await fetch(`${apiUrl}/profile`, {
      headers: { "Authorization": `Bearer ${jwtToken}` }
    });
    if (response.ok) {
      const data = await response.json();
      document.getElementById("profileBalance").innerText = `$${data.balance.toFixed(2)}`;
    }
  } catch (e) {
    console.error("Error loading profile balance:", e);
  }

  updateNotifStatusDisplay();
}

// ─────────────────────────────────────────────────────────────────────────────
// Notification Permissions & Token Sync
// ─────────────────────────────────────────────────────────────────────────────

function updateNotifStatusDisplay() {
  const display = document.getElementById("notifStatus");
  if (Notification.permission === "default") {
    display.innerText = "Prompt pending";
    display.style.color = "var(--yellow)";
  } else if (Notification.permission === "granted") {
    display.innerText = "Granted (Subscribed)";
    display.style.color = "var(--green)";
    document.getElementById("requestNotifBtn").style.display = "none";
  } else {
    display.innerText = "Blocked";
    display.style.color = "var(--red)";
  }
}

async function requestNotificationPermission() {
  try {
    const permission = await Notification.requestPermission();
    updateNotifStatusDisplay();
    if (permission === "granted") {
      syncPushToken();
    }
  } catch (e) {
    showAlert("Failed to request permissions", "error");
  }
}

async function syncPushToken() {
  if (!jwtToken) return;

  let pushToken = "";

  if (messaging) {
    try {
      pushToken = await messaging.getToken({ vapidKey: 'YOUR_VAPID_KEY' });
      console.log("Acquired FCM Token:", pushToken);
    } catch (e) {
      console.error("Could not fetch FCM token:", e);
      showAlert("Firebase Token sync failed. Defaulting to mock local push notifications.", "error");
      pushToken = `mock_fcm_token_${username}`;
    }
  } else {
    pushToken = `mock_fcm_token_${username}`;
  }

  // Upload token to backend server
  try {
    const response = await fetch(`${apiUrl}/fcm-token`, {
      method: "PUT",
      headers: {
        "Content-Type": "application/json",
        "Authorization": `Bearer ${jwtToken}`
      },
      body: JSON.stringify({ fcm_token: pushToken })
    });
    if (response.ok) {
      showAlert("Notifications registered on server!", "success");
    } else {
      console.error("Failed to upload token to server");
    }
  } catch (e) {
    console.error("Failed to connect to server token update:", e);
  }
}

// ─────────────────────────────────────────────────────────────────────────────
// UI Helpers
// ─────────────────────────────────────────────────────────────────────────────

function showAlert(message, type) {
  const banner = document.getElementById("alertBanner");
  banner.innerText = message;
  banner.className = `alert-banner ${type}`;
  banner.style.display = "block";
  setTimeout(() => {
    banner.style.display = "none";
  }, 4000);
}

function showLocalNotification(title, body) {
  if (Notification.permission === 'granted') {
    navigator.serviceWorker.ready.then(reg => {
      reg.showNotification(title, {
        body: body,
        icon: 'icon-192.png',
        vibrate: [200, 100, 200],
        tag: 'lms-alert'
      });
    });
  }
}
