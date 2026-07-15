const dropzone = document.getElementById('dropzone');
const coverInput = document.getElementById('coverInput');
const previewBox = document.getElementById('coverPreview');
const themeToggle = document.getElementById('themeToggle');
const authOverlay = document.getElementById('authOverlay');
const authToggle = document.getElementById('authToggle');
const authPasswordInput = document.getElementById('authPassword');
const authSubmit = document.getElementById('authSubmit');
const authSkip = document.getElementById('authSkip');
const authMessage = document.getElementById('authMessage');
const heroSlides = Array.from(document.querySelectorAll('.hero-carousel__slide'));

const themes = [
  { id: 'theme-0', label: '🌑 Midnight' },
  { id: 'theme-1', label: '🌊 Azure' },
  { id: 'theme-2', label: '🌿 Forest' },
  { id: 'theme-3', label: '🍃 Soft Lime' },
  { id: 'theme-4', label: '🌾 Olive' },
  { id: 'dark', label: '🌙 Dark Mode' }
];

let currentThemeIndex = 0;

function applyTheme(index) {
  currentThemeIndex = index;
  document.body.setAttribute('data-theme', themes[index].id);
  if (themeToggle) {
    themeToggle.textContent = `🎨 ${themes[index].label}`;
  }
}

function nextTheme() {
  const nextIndex = (currentThemeIndex + 1) % themes.length;
  applyTheme(nextIndex);
}

if (themeToggle) {
  themeToggle.addEventListener('click', (event) => {
    event.preventDefault();
    nextTheme();
    document.body.classList.add('theme-switched');
    setTimeout(() => document.body.classList.remove('theme-switched'), 500);
  });
}

function hideAuthOverlay(isAdmin = false) {
  if (authOverlay) {
    authOverlay.style.display = 'none';
    document.body.classList.remove('locked');
  }
  if (isAdmin) {
    document.body.classList.add('admin-active');
    document.body.classList.remove('guest-active');
  }
}

function showAuthOverlay() {
  if (authOverlay) {
    authOverlay.style.display = 'flex';
    document.body.classList.add('locked');
    authPasswordInput?.focus();
  }
}

function showAuthMessage(text) {
  if (authMessage) {
    authMessage.textContent = text;
  }
}

function authenticate(password) {
  return fetch('/auth', {
    method: 'POST',
    headers: {
      'Content-Type': 'application/json',
    },
    body: JSON.stringify({ password }),
  }).then((response) => response.json());
}

if (authToggle) {
  authToggle.addEventListener('click', () => {
    showAuthOverlay();
    showAuthMessage('');
  });
}

if (authPasswordInput) {
  authPasswordInput.addEventListener('keydown', async (event) => {
    if (event.key === 'Enter') {
      event.preventDefault();
      authSubmit?.click();
    }
  });
}

if (authSubmit) {
  authSubmit.addEventListener('click', async () => {
    const value = authPasswordInput?.value || '';
    const result = await authenticate(value.trim());
    if (result.admin) {
      hideAuthOverlay(true);
      showAuthMessage('Mật khẩu chính xác. Bạn đã đăng nhập admin.');
      window.setTimeout(() => {
        window.location.href = result.redirect || '/';
      }, 240);
    } else {
      hideAuthOverlay(false);
      showAuthMessage('Mật khẩu sai. Chế độ xem khách đã được kích hoạt.');
    }
  });
}

if (authSkip) {
  authSkip.addEventListener('click', () => {
    hideAuthOverlay(false);
    showAuthMessage('Bạn đã bỏ qua xác thực. Chế độ xem khách đã được kích hoạt.');
  });
}

function updatePreview(file) {
  if (!previewBox) return;
  previewBox.innerHTML = '';
  if (!file) {
    previewBox.innerHTML = '<span>Preview will appear here</span>';
    return;
  }

  const reader = new FileReader();
  reader.onload = function (event) {
    const img = document.createElement('img');
    img.src = event.target.result;
    img.alt = 'Cover preview';
    previewBox.appendChild(img);
  };
  reader.readAsDataURL(file);
}

applyTheme(0);

if (authOverlay) {
  authOverlay.style.display = 'none';
  document.body.classList.remove('locked');
}

if (heroSlides.length > 1) {
  let heroActiveIndex = 0;
  setInterval(() => {
    heroActiveIndex = (heroActiveIndex + 1) % heroSlides.length;
    heroSlides.forEach((slide, index) => {
      slide.classList.toggle('is-active', index === heroActiveIndex);
    });
  }, 4000);
}

if (dropzone && coverInput) {
  ['dragenter', 'dragover'].forEach((eventName) => {
    dropzone.addEventListener(eventName, (event) => {
      event.preventDefault();
      dropzone.classList.add('is-dragover');
    });
  });

  ['dragleave', 'dragend', 'drop'].forEach((eventName) => {
    dropzone.addEventListener(eventName, (event) => {
      event.preventDefault();
      dropzone.classList.remove('is-dragover');
    });
  });

  dropzone.addEventListener('drop', (event) => {
    const file = event.dataTransfer?.files?.[0];
    if (file) {
      coverInput.files = event.dataTransfer.files;
      const label = dropzone.querySelector('p');
      if (label) {
        label.textContent = `Ảnh bìa đã chọn: ${file.name}`;
      }
      updatePreview(file);
    }
  });

  coverInput.addEventListener('change', () => {
    const file = coverInput.files?.[0];
    if (file) {
      const label = dropzone.querySelector('p');
      if (label) {
        label.textContent = `Ảnh bìa đã chọn: ${file.name}`;
      }
      updatePreview(coverInput.files?.[0]);
    }
  });
}
