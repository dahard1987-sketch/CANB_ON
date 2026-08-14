const dropzone = document.querySelector('#dropzone');
const fileInput = document.querySelector('#fileInput');
const browseButton = document.querySelector('#browseButton');
const publishButton = document.querySelector('#publishButton');
const notice = document.querySelector('#notice');
const fileCount = document.querySelector('#fileCount');
const readyCount = document.querySelector('#readyCount');
const levelSlots = document.querySelector('#levelSlots');

let batchSlots = {};
let busy = false;

function showNotice(message, isError = false) {
  notice.textContent = message;
  notice.hidden = !message;
  notice.classList.toggle('error', isError);
}

function stateLabel(status) {
  return {
    ready: '준비됨',
    uploading: '업데이트 중',
    success: '완료',
    failed: '실패',
  }[status] || '대기';
}

function renderBatch() {
  document.querySelectorAll('.level-slot').forEach((slot) => {
    const item = batchSlots[slot.dataset.level];
    const filename = slot.querySelector('.slot-filename');
    const error = slot.querySelector('.slot-error');
    const state = slot.querySelector('.slot-state');

    slot.classList.remove('has-file', 'uploading', 'success', 'failed');
    if (!item) {
      filename.textContent = '파일 없음';
      error.textContent = '';
      state.textContent = '대기';
      return;
    }

    slot.classList.add('has-file', item.status);
    filename.textContent = `${item.filename} · ${item.rows.toLocaleString()}행`;
    filename.title = item.filename;
    error.textContent = item.error || '';
    error.title = item.error || '';
    state.textContent = stateLabel(item.status);
  });

  const items = Object.values(batchSlots);
  const actionable = items.filter(
    (item) => (item.status === 'ready' || item.status === 'failed') && item.file,
  );
  fileCount.textContent = `${items.length} / ${levelSlots.dataset.total}`;
  readyCount.textContent = actionable.length;
  publishButton.disabled = busy || actionable.length === 0;
}

async function parseResponse(response) {
  let payload;
  try {
    payload = await response.json();
  } catch {
    throw new Error('서버 응답을 읽을 수 없습니다.');
  }
  if (!response.ok) {
    const error = new Error(payload.error || '요청을 처리하지 못했습니다.');
    error.payload = payload;
    throw error;
  }
  return payload;
}

async function stageFiles(files) {
  const excelFiles = [...files].filter((file) => /\.(xlsx|xlsm)$/i.test(file.name));
  if (!excelFiles.length) {
    showNotice('.xlsx 또는 .xlsm 파일을 선택해 주세요.', true);
    return;
  }

  busy = true;
  dropzone.classList.add('is-busy');
  dropzone.setAttribute('aria-busy', 'true');
  publishButton.disabled = true;
  showNotice(`${excelFiles.length}개 파일의 G열을 확인하고 있습니다…`);

  const form = new FormData();
  excelFiles.forEach((file) => form.append('files', file));

  try {
    const response = await fetch('/api/files', { method: 'POST', body: form });
    const payload = await parseResponse(response);
    const accepted = payload.results.filter((result) => result.accepted);
    const rejected = payload.results.filter((result) => !result.accepted);
    let replaced = 0;

    accepted.forEach((result) => {
      if (batchSlots[result.level]) replaced += 1;
      batchSlots[result.level] = {
        level: result.level,
        filename: result.filename,
        rows: result.rows,
        status: 'ready',
        error: null,
        file: excelFiles[result.index],
      };
    });

    const messages = [];
    if (accepted.length) messages.push(`${accepted.length}개 파일을 레벨별로 등록했습니다.`);
    if (replaced) messages.push(`${replaced}개 레벨의 기존 파일은 새 파일로 교체했습니다.`);
    if (rejected.length) {
      messages.push(rejected.map((item) => `${item.filename}: ${item.error}`).join(' / '));
    }
    showNotice(messages.join(' '), rejected.length > 0);
  } catch (error) {
    showNotice(error.message, true);
  } finally {
    busy = false;
    dropzone.classList.remove('is-busy');
    dropzone.removeAttribute('aria-busy');
    fileInput.value = '';
    renderBatch();
  }
}

function removeLevel(level) {
  if (busy || !batchSlots[level]) return;
  delete batchSlots[level];
  renderBatch();
  showNotice(`${level} 파일을 목록에서 제거했습니다.`);
}

async function publishAll() {
  if (busy) return;
  const actionable = Object.values(batchSlots).filter(
    (item) => (item.status === 'ready' || item.status === 'failed') && item.file,
  );
  if (!actionable.length) return;

  busy = true;
  actionable.forEach((item) => {
    item.status = 'uploading';
    item.error = null;
  });
  publishButton.setAttribute('aria-busy', 'true');
  publishButton.classList.add('is-busy');
  publishButton.querySelector('span').textContent = 'Google Sheets 업데이트 중…';
  renderBatch();
  showNotice('파일을 다시 검증한 뒤 Google Sheets를 업데이트하고 있습니다. 창을 닫지 마세요.');

  const form = new FormData();
  actionable.forEach((item) => form.append('files', item.file, item.filename));

  try {
    const response = await fetch('/api/publish', { method: 'POST', body: form });
    const payload = await parseResponse(response);

    Object.entries(payload.slots).forEach(([level, result]) => {
      const previous = batchSlots[level];
      batchSlots[level] = {
        ...previous,
        ...result,
        file: result.status === 'failed' ? previous.file : null,
      };
    });

    if (payload.summary.failed) {
      showNotice(
        `완료 ${payload.summary.success}개, 실패 ${payload.summary.failed}개입니다. 실패 항목을 확인해 주세요.`,
        true,
      );
    } else {
      showNotice(`${payload.summary.success}개 파일을 모두 업데이트했습니다.`);
    }
  } catch (error) {
    actionable.forEach((item) => {
      if (item.status === 'uploading') {
        item.status = 'failed';
        item.error = error.message;
      }
    });
    showNotice(error.message, true);
  } finally {
    busy = false;
    publishButton.removeAttribute('aria-busy');
    publishButton.classList.remove('is-busy');
    publishButton.querySelector('span').textContent = '등록된 모든 파일 업데이트';
    renderBatch();
  }
}

['dragenter', 'dragover'].forEach((eventName) => {
  dropzone.addEventListener(eventName, (event) => {
    event.preventDefault();
    if (!busy) dropzone.classList.add('is-dragging');
  });
});

['dragleave', 'drop'].forEach((eventName) => {
  dropzone.addEventListener(eventName, (event) => {
    event.preventDefault();
    dropzone.classList.remove('is-dragging');
  });
});

dropzone.addEventListener('drop', (event) => {
  if (!busy) stageFiles(event.dataTransfer.files);
});
dropzone.addEventListener('click', (event) => {
  if (!busy && event.target !== browseButton) fileInput.click();
});
dropzone.addEventListener('keydown', (event) => {
  if (!busy && (event.key === 'Enter' || event.key === ' ')) {
    event.preventDefault();
    fileInput.click();
  }
});
browseButton.addEventListener('click', (event) => {
  event.stopPropagation();
  if (!busy) fileInput.click();
});
fileInput.addEventListener('change', () => stageFiles(fileInput.files));
publishButton.addEventListener('click', publishAll);

document.querySelectorAll('.level-slot').forEach((slot) => {
  slot.querySelector('.remove-button').addEventListener('click', () => removeLevel(slot.dataset.level));
});

renderBatch();
