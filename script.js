const dropzone = document.getElementById('dropzone');
const coverInput = document.getElementById('coverInput');

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
    }
  });

  coverInput.addEventListener('change', () => {
    const file = coverInput.files?.[0];
    if (file) {
      const label = dropzone.querySelector('p');
      if (label) {
        label.textContent = `Ảnh bìa đã chọn: ${file.name}`;
      }
    }
  });
}
