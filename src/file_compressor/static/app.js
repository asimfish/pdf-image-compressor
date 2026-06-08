function _trapFocus(el) {
  if (el._focusTrap) el.removeEventListener('keydown', el._focusTrap);
  const focusable = () => el.querySelectorAll('button, [href], input, select, textarea, [tabindex]:not([tabindex="-1"])');
  el._focusTrap = (e) => {
    if (e.key !== 'Tab') return;
    const items = focusable();
    if (!items.length) return;
    const first = items[0], last = items[items.length - 1];
    if (e.shiftKey && document.activeElement === first) { e.preventDefault(); last.focus(); }
    else if (!e.shiftKey && document.activeElement === last) { e.preventDefault(); first.focus(); }
  };
  el.addEventListener('keydown', el._focusTrap);
  const first = focusable()[0];
  if (first) first.focus();
}

function app() {
  return {
    pdfs: [],
    versionCache: {},
    _versionGen: {},
    versionLoading: {},
    _pdfMeta: {},
    stats: null,
    search: '',
    sortBy: 'date',
    filterTab: 'All',
    presets: ['200KB', '500KB', '1MB', '2MB', '5MB'],
    pdfModeOptions: [
      { value: 'auto', label: 'Auto' },
      { value: 'optimize', label: 'Optimize (keep text)' },
      { value: 'raster', label: 'Raster (max compression)' },
    ],
    expanded: null,
    loading: true,
    apiError: false,
    batchCompressing: false,
    showBatchCompress: false,
    batchForm: { quality: 82, target_size: '', pdf_mode: 'auto', pdf_dpi: 120, pdf_grayscale: false, strip_metadata: true, label: '' },
    batchResults: null,
    selectMode: false,
    selectedPdfs: {},
    // Upload
    showUpload: false,
    uploadFiles: [],
    uploading: false,
    uploadProgress: 0,
    uploadStatus: '',
    dragOver: false,
    globalDragActive: false,
    _globalDragCounter: 0,
    uploadForm: { quality: 82, target_size: '', pdf_mode: 'auto', pdf_dpi: 120, pdf_grayscale: false, strip_metadata: true, notes: '' },
    // Compress
    showCompress: false,
    compressTarget: null,
    compressing: false,
    compressForm: { quality: 82, target_size: '500KB', pdf_mode: 'auto', pdf_dpi: 120, pdf_grayscale: false, strip_metadata: true, label: '' },
    compressResult: null,
    // Notes
    batchTargetPdfs: [],
    showNotes: false,
    notesTarget: null,
    notesText: '',
    showShortcuts: false,
    // Compare
    showCompare: false,
    comparePdf: null,
    compareVersion: null,
    comparePage: 0,
    compareTotalPages: 1,
    compareSlider: 50,
    _compareDragging: false,
    compareLoading: false,
    compareError: '',
    _compareLoaded: 0,
    _compareGen: 0,
    // Toast
    toast: '',
    toastType: 'success',
    _defaults(extra = {}) {
      return { quality: 82, target_size: '', pdf_mode: 'auto', pdf_dpi: 120, pdf_grayscale: false, strip_metadata: true, ...extra };
    },
    _buildCompressFd(form, extras) {
      const fd = new FormData();
      for (const [k, v] of Object.entries(form)) {
        if (v !== '' && v != null) fd.append(k, v);
      }
      if (extras) {
        for (const [k, v] of Object.entries(extras)) fd.append(k, v);
      }
      return fd;
    },
    _evictPdfCache(pdfId) {
      const { [pdfId]: _pm, ...restMeta } = this._pdfMeta;
      this._pdfMeta = restMeta;
      const { [pdfId]: _vc, ...restVersions } = this.versionCache;
      this.versionCache = restVersions;
      const { [pdfId]: _vl, ...restLoading } = this.versionLoading;
      this.versionLoading = restLoading;
    },
    _validTargetSize(v) {
      if (!v || !v.trim()) return '';
      if (!/^\s*\d+(\.\d+)?\s*[kmgt]?b\s*$/i.test(v)) return 'Enter a size with unit (e.g. 500KB, 2MB)';
      const num = parseFloat(v);
      if (num === 0) return 'Target size must be greater than zero';
      return '';
    },
    _validateForm(form) {
      if (!(form.quality >= 1 && form.quality <= 95)) return 'Quality must be between 1 and 95';
      if (!(form.pdf_dpi >= 36 && form.pdf_dpi <= 300)) return 'DPI must be between 36 and 300';
      return this._validTargetSize(form.target_size);
    },
    get uploadValidation() { return this._validateForm(this.uploadForm); },
    get compressValidation() { return this._validateForm(this.compressForm); },
    get batchCompressValidation() { return this._validateForm(this.batchForm); },
    get allFilteredSelected() {
      const fids = new Set(this.filteredPdfs.map(p => p.id));
      const sids = new Set(Object.keys(this.selectedPdfs));
      return fids.size > 0 && fids.size === sids.size && [...fids].every(id => sids.has(id));
    },
    get filteredPdfs() {
      let list = this.pdfs;
      if (this.search.trim()) {
        const q = this.search.toLowerCase();
        list = list.filter(p => p.filename.toLowerCase().includes(q) || (p.notes || '').toLowerCase().includes(q));
      }
      if (this.filterTab === 'Uncompressed') {
        list = list.filter(p => (p.version_count || 0) === 0);
      } else if (this.filterTab === 'Compressed') {
        list = list.filter(p => (p.version_count || 0) > 0);
      }
      const sorted = [...list];
      switch (this.sortBy) {
        case 'name': sorted.sort((a, b) => a.filename.localeCompare(b.filename)); break;
        case 'size': sorted.sort((a, b) => b.file_size - a.file_size); break;
        case 'savings': sorted.sort((a, b) => this.bestSaving(b) - this.bestSaving(a)); break;
        default: sorted.sort((a, b) => b.upload_time.localeCompare(a.upload_time)); break;
      }
      return sorted;
    },
    get uncompressedCount() {
      return this.pdfs.filter(p => (p.version_count || 0) === 0).length;
    },
    get compressedCount() {
      return this.pdfs.filter(p => (p.version_count || 0) > 0).length;
    },
    async loadLibrary() {
      const isInitial = !this._libraryLoaded;
      this.loading = isInitial;
      this.apiError = false;
      try {
        const pdfsRes = await fetch('/api/pdfs');
        if (!pdfsRes.ok) throw new Error('Failed to load PDFs');
        this.pdfs = await pdfsRes.json();
        this._pdfMeta = {};
        fetch('/api/stats').then(r => r.ok ? r.json() : null).then(d => { if (d) this.stats = d; }).catch(() => {});
      } catch (e) {
        this.apiError = true;
        this.showToast('Failed to load library: ' + e.message, 'error');
      }
      this._libraryLoaded = true;
      this.loading = false;
    },
    async loadVersions(pdfId) {
      const gen = (this._versionGen[pdfId] || 0) + 1;
      this._versionGen[pdfId] = gen;
      this.versionLoading[pdfId] = true;
      try {
        const res = await fetch(`/api/pdfs/${pdfId}/versions`);
        if (!res.ok) throw new Error('Failed to load versions');
        if (this._versionGen[pdfId] === gen) {
          this.versionCache[pdfId] = await res.json();
        }
      } catch (e) {
        if (this._versionGen[pdfId] === gen) {
          this.showToast('Failed to load versions: ' + e.message, 'error');
        }
      }
      if (this._versionGen[pdfId] === gen) {
        this.versionLoading[pdfId] = false;
      }
    },
    toggleExpand(pdfId) {
      this.expanded = this.expanded === pdfId ? null : pdfId;
      if (this.expanded && !this.versionCache[pdfId]) this.loadVersions(pdfId);
    },
    toggleSelect(pdfId) {
      const n = { ...this.selectedPdfs };
      n[pdfId] ? delete n[pdfId] : n[pdfId] = true;
      this.selectedPdfs = n;
    },
    selectAll() {
      this.selectedPdfs = this.allFilteredSelected ? {} : Object.fromEntries(this.filteredPdfs.map(p => [p.id, true]));
    },
    async batchDelete() {
      const ids = Object.keys(this.selectedPdfs);
      if (!confirm(`Delete ${this._plural(ids.length, 'PDF')} and all their versions?`)) return;
      try {
        const res = await fetch('/api/pdfs/batch-delete', {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({ pdf_ids: ids }),
        });
        if (!res.ok) throw new Error('Batch delete failed');
        const data = await res.json();
        this.showToast(`Deleted ${data.deleted} PDFs`);
        ids.forEach(id => this._evictPdfCache(id));
        if (ids.includes(this.expanded)) this.expanded = null;
        if (this.showCompare && ids.includes(this.comparePdf?.id)) this.showCompare = false;
        this.selectedPdfs = {};
        this.selectMode = false;
        this.loadLibrary();
      } catch (e) {
        this.showToast('Delete failed: ' + e.message, 'error');
      }
    },
    handleDrop(e) {
      this.dragOver = false;
      this._selectFiles([...e.dataTransfer.files]);
    },
    _onGlobalDragEnter(e) {
      e.preventDefault();
      this._globalDragCounter++;
      if (e.dataTransfer.types.includes('Files')) {
        this.globalDragActive = true;
      }
    },
    _onGlobalDragOver(e) {
      e.preventDefault();
      e.dataTransfer.dropEffect = 'copy';
    },
    _onGlobalDragLeave(e) {
      this._globalDragCounter--;
      if (this._globalDragCounter <= 0) {
        this._globalDragCounter = 0;
        this.globalDragActive = false;
      }
    },
    _onGlobalDrop(e) {
      e.preventDefault();
      this._globalDragCounter = 0;
      this.globalDragActive = false;
      const files = [...e.dataTransfer.files];
      if (!files.length) return;
      if (this.selectMode || this.showCompress || this.showBatchCompress || this.showNotes || this.showCompare) return;
      this.showUpload = true;
      this._selectFiles(files);
    },
    handleFiles(files) {
      this._selectFiles([...files]);
    },
    _selectFiles(all) {
      const MAX = 500 * 1024 * 1024;
      const pdfs = all.filter(f => f.name.toLowerCase().endsWith('.pdf'));
      if (!pdfs.length) { this.showToast('Only PDF files are supported', 'error'); return; }
      const valid = pdfs.filter(f => f.size <= MAX);
      const nonPdfCount = all.length - pdfs.length;
      const tooLargeCount = pdfs.length - valid.length;
      const existing = new Set(this.uploadFiles.map(f => `${f.name}:${f.size}`));
      const newFiles = valid.filter(f => !existing.has(`${f.name}:${f.size}`));
      const dupes = valid.length - newFiles.length;
      this.uploadFiles = [...this.uploadFiles, ...newFiles];
      // Build a single toast message
      const parts = [];
      const skipped = [];
      if (nonPdfCount > 0) skipped.push(`${nonPdfCount} non-PDF`);
      if (tooLargeCount > 0) skipped.push(`${tooLargeCount} over 500MB limit`);
      if (skipped.length) parts.push(`Skipped ${skipped.join(', ')}`);
      if (newFiles.length > 0) parts.push(`Added ${this._plural(newFiles.length, 'PDF')}`);
      if (dupes > 0) parts.push(this._plural(dupes, 'duplicate'));
      if (!parts.length) { this.showToast('No new files to add', 'error'); return; }
      const isError = newFiles.length === 0;
      this.showToast(parts.join(', '), isError ? 'error' : 'success');
    },
    _uploadWithProgress(file, fd, index, total) {
      return new Promise((resolve, reject) => {
        const xhr = new XMLHttpRequest();
        this._currentXhr = xhr;
        xhr.open('POST', '/api/pdfs/upload');
        xhr.upload.onprogress = (e) => {
          if (e.lengthComputable) {
            const filePct = e.loaded / e.total;
            this.uploadProgress = Math.round(((index + filePct) / total) * 100);
            if (filePct >= 1) this.uploadStatus = `Processing ${index + 1}/${total}: ${file.name}`;
          }
        };
        xhr.onload = () => {
          this._currentXhr = null;
          if (xhr.status >= 200 && xhr.status < 300) {
            try { resolve(JSON.parse(xhr.responseText)); } catch { reject(new Error('Invalid response')); }
          } else {
            try { reject(new Error(JSON.parse(xhr.responseText).detail || xhr.statusText)); }
            catch { reject(new Error(xhr.statusText)); }
          }
        };
        xhr.onerror = () => { this._currentXhr = null; reject(new Error('Network error')); };
        xhr.onabort = () => { this._currentXhr = null; reject(new Error('Upload cancelled')); };
        xhr.send(fd);
      });
    },
    async doUpload() {
      if (!this.uploadFiles.length) return;
      this._uploadCancelled = false;
      this.uploading = true;
      this.uploadProgress = 0;
      this.uploadStatus = 'Uploading...';
      let succeeded = 0; const errors = []; const warnings = []; const compressResults = []; let uploadedPdf = null; const totalFiles = this.uploadFiles.length;
      const { notes, ...formFields } = this.uploadForm;
      for (let i = 0; i < this.uploadFiles.length; i++) {
        const fd = this._buildCompressFd(formFields, { file: this.uploadFiles[i], notes });
        this.uploadStatus = `Uploading ${i + 1}/${this.uploadFiles.length}: ${this.uploadFiles[i].name}`;
        try {
          const data = await this._uploadWithProgress(this.uploadFiles[i], fd, i, this.uploadFiles.length);
          if (data.warning) warnings.push(data.warning);
          if (data.file_size && data.best_compressed_size) {
            compressResults.push({ original: data.file_size, compressed: data.best_compressed_size });
          }
          uploadedPdf = data; succeeded++;
        } catch (e) {
          if (!this.uploading) break;
          errors.push(`${this.uploadFiles[i].name}: ${e.message}`);
        }
        if (!this.uploading) break;
      }
      if (succeeded === 0 && this._uploadCancelled) {
        this.showUpload = false; this.uploadFiles = []; this.showToast('Upload cancelled', 'error'); return;
      }
      this.uploadProgress = 100; this.uploading = false;
      if (succeeded === 0 && errors.length > 0) {
        this.showToast(`Upload failed: ${errors.join('; ')}`, 'error');
        return;
      }
      if (this._uploadCancelled && succeeded > 0) {
        this.uploadFiles = this.uploadFiles.slice(succeeded);
        this.uploading = false;
        this.uploadProgress = 0;
        this.showToast(`Uploaded ${succeeded}/${totalFiles} PDFs (cancelled, ${this.uploadFiles.length} remaining)`, 'warning');
        this.loadLibrary();
        return;
      }
      this.showUpload = false; this.uploadFiles = [];
      this.uploadForm = this._defaults({ notes: '' });
      this.showToast(this._buildUploadToast(succeeded, compressResults, warnings, errors), errors.length ? 'error' : 'success');
      this.loadLibrary();
      if (totalFiles === 1 && succeeded === 1) this.compressPdf(uploadedPdf);
    },
    _buildUploadToast(succeeded, compressResults, warnings, errors) {
      const totalSaved = compressResults.reduce((s, r) => s + (r.original - r.compressed), 0);
      const parts = [`Uploaded ${this._plural(succeeded, 'PDF')}`];
      if (totalSaved > 0) parts.push(`saved ${this.fmtSize(totalSaved)}`);
      if (warnings.length) parts.push(this._plural(warnings.length, 'warning'));
      if (errors.length) parts.push(`${errors.length} failed: ${errors.join('; ')}`);
      return parts.join(', ');
    },
    cancelUpload() {
      this._uploadCancelled = true;
      this.uploading = false;
      if (this._currentXhr) { this._currentXhr.abort(); this._currentXhr = null; }
    },
    closeUpload() {
      if (this.uploading) { this.cancelUpload(); }
      else if (this.uploadFiles.length > 0 && !confirm('Discard selected files?')) return;
      this.showUpload = false;
      this.uploadFiles = [];
    },
    batchCompress() {
      this.batchForm = this._defaults({ label: '' });
      this.batchTargetPdfs = this.filteredPdfs;
      this._batchTotalAtOpen = this.pdfs.length;
      this.batchResults = null;
      this.showBatchCompress = true;
    },
    async doBatchCompress() {
      this.batchCompressing = true;
      this.batchResults = null;
      this._batchAbort = new AbortController();
      try {
        const fd = this._buildCompressFd(this.batchForm);
        if (this.batchTargetPdfs.length < this.pdfs.length) {
          fd.append('pdf_ids', this.batchTargetPdfs.map(p => p.id).join(','));
        }
        const res = await fetch('/api/pdfs/batch-compress', { method: 'POST', body: fd, signal: this._batchAbort.signal });
        const data = await res.json().catch(() => ({}));
        if (!res.ok) throw new Error(data.detail || 'Batch compress failed');
        this.batchResults = data;
        this.loadLibrary();
      } catch (e) {
        if (e.name === 'AbortError') {
          this.showToast('Batch compress cancelled', 'error');
        } else {
          this.showToast('Batch compress failed: ' + e.message, 'error');
        }
      }
      this._batchAbort = null;
      this.batchCompressing = false;
    },
    cancelBatchCompress() {
      if (this._batchAbort) this._batchAbort.abort();
      this.showBatchCompress = false;
      this.batchResults = null;
      this.loadLibrary();
    },
    closeBatchResults() {
      this.batchResults = null;
      this.showBatchCompress = false;
    },
    compressPdf(pdf) {
      this.compressTarget = pdf;
      this.compressResult = null;
      const bv = this.bestVersion(pdf);
      const base = bv ? bv.file_size : pdf.file_size;
      const target = base ? Math.max(10000, Math.round(base * 0.8)) : 500000;
      this.compressForm = this._defaults({ target_size: this.fmtSize(target), label: '' });
      this.showCompress = true;
    },
    reuseSettings(pdf, version) {
      this.compressTarget = pdf;
      this.compressResult = null;
      const d = this._defaults({});
      this.compressForm = {
        quality: version.quality ?? d.quality,
        target_size: version.target_bytes ? this.fmtSize(version.target_bytes) : '',
        pdf_mode: version.pdf_mode ?? d.pdf_mode,
        pdf_dpi: version.pdf_dpi ?? d.pdf_dpi,
        pdf_grayscale: version.pdf_grayscale ?? false,
        strip_metadata: version.strip_metadata !== false,
        label: '',
      };
      this.showCompress = true;
    },
    async doCompress() {
      if (!this.compressTarget) return;
      this.compressing = true;
      this.compressResult = null;
      try {
        const fd = this._buildCompressFd(this.compressForm);
        const res = await fetch(`/api/pdfs/${this.compressTarget.id}/compress`, { method: 'POST', body: fd });
        const ver = await res.json().catch(() => ({}));
        if (!res.ok) throw new Error(ver.detail || 'Compression failed');
        this.compressResult = ver;
        this.loadVersions(this.compressTarget.id);
        this.loadLibrary();
      } catch (e) {
        this.showToast('Compression failed: ' + e.message, 'error');
      }
      this.compressing = false;
    },
    closeCompress() {
      this.compressResult = null;
      this.showCompress = false;
      this.compressTarget = null;
    },
    editNotes(pdf) {
      this.notesTarget = pdf;
      this.notesText = pdf.notes || '';
      this.showNotes = true;
    },
    async saveNotes() {
      if (!this.notesTarget) return;
      try {
        const res = await fetch(`/api/pdfs/${this.notesTarget.id}/notes`, {
          method: 'PUT',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({ notes: this.notesText }),
        });
        if (!res.ok) throw new Error('Failed to save notes');
        this.showNotes = false;
        this.loadLibrary();
      } catch (e) {
        this.showToast('Save failed: ' + e.message, 'error');
      }
    },
    async deletePdf(pdf) {
      if (!confirm(`Delete "${pdf.filename}" and all versions?`)) return;
      try {
        const res = await fetch(`/api/pdfs/${pdf.id}`, { method: 'DELETE' });
        if (!res.ok) throw new Error('Delete failed');
        if (this.expanded === pdf.id) this.expanded = null;
        if (this.showCompare && this.comparePdf?.id === pdf.id) this.showCompare = false;
        this._evictPdfCache(pdf.id);
        this.showToast('Deleted');
        this.loadLibrary();
      } catch (e) {
        this.showToast('Delete failed: ' + e.message, 'error');
      }
    },
    async deleteVersion(pdfId, versionId) {
      if (!confirm('Delete this version?')) return;
      try {
        const res = await fetch(`/api/versions/${versionId}`, { method: 'DELETE' });
        if (!res.ok) throw new Error('Delete failed');
        if (this.showCompare && this.compareVersion?.id === versionId) this.showCompare = false;
        const { [pdfId]: _pm, ...rest } = this._pdfMeta;
        this._pdfMeta = rest;
        this.loadVersions(pdfId);
        this.loadLibrary();
      } catch (e) {
        this.showToast('Delete failed: ' + e.message, 'error');
      }
    },
    // Compare
    openCompare(pdf, version) {
      this.comparePdf = pdf;
      this.compareVersion = version;
      this.comparePage = 0;
      this.compareTotalPages = Math.min(pdf.page_count || 1, version.page_count || pdf.page_count || 1);
      this.compareSlider = 50;
      this._resetCompareLoading();
      this.showCompare = true;
    },
    comparePrev() { if (this.comparePage > 0) { this.comparePage--; this._resetCompareLoading(); } },
    compareNext() { if (this.comparePage < this.compareTotalPages - 1) { this.comparePage++; this._resetCompareLoading(); } },
    _resetCompareLoading() {
      this.compareLoading = true;
      this.compareError = '';
      this._compareLoaded = 0;
      this._compareGen++;
      const gen = this._compareGen;
      setTimeout(() => {
        if (gen === this._compareGen && this.compareLoading) {
          this.compareLoading = false;
          this.compareError = 'Loading timed out — try again';
        }
      }, 15000);
    },
    _onCompareLoad(e) {
      const m = /[?&]_=(\d+)/.exec(e.target.src);
      if (!m || +m[1] !== this._compareGen) return;
      this._compareLoaded++;
      if (this._compareLoaded >= 2) {
        this.compareLoading = false;
        if (!this.compareError) this.compareError = '';
      }
    },
    _onCompareError(e) {
      const m = /[?&]_=(\d+)/.exec(e.target.src);
      if (!m || +m[1] !== this._compareGen) return;
      this._compareLoaded++;
      this.compareError = 'Failed to load preview';
      if (this._compareLoaded >= 2) this.compareLoading = false;
    },
    compareOriginalUrl() {
      return `/api/preview/original/${this.comparePdf.id}/${this.comparePage}?_=${this._compareGen}`;
    },
    compareVersionUrl() {
      return `/api/preview/version/${this.compareVersion.id}/${this.comparePage}?_=${this._compareGen}`;
    },
    _cleanupCompareListeners() {
      this._compareDragging = false;
      if (this._onMouseMove) {
        window.removeEventListener('mousemove', this._onMouseMove);
        window.removeEventListener('touchmove', this._onMouseMove);
        this._onMouseMove = null;
      }
      if (this._onMouseUp) {
        window.removeEventListener('mouseup', this._onMouseUp);
        window.removeEventListener('touchend', this._onMouseUp);
        this._onMouseUp = null;
      }
    },
    _onCompareMouseDown(e) {
      this._cleanupCompareListeners();
      this._compareDragging = true;
      this._updateCompareSlider(e);
      this._onMouseMove = (ev) => { if (this._compareDragging) { if (ev.touches) ev.preventDefault(); this._updateCompareSlider(ev); } };
      this._onMouseUp = () => { this._compareDragging = false; this._cleanupCompareListeners(); };
      window.addEventListener('mousemove', this._onMouseMove);
      window.addEventListener('mouseup', this._onMouseUp);
      window.addEventListener('touchmove', this._onMouseMove, { passive: false });
      window.addEventListener('touchend', this._onMouseUp);
    },
    _updateCompareSlider(e) {
      const container = this.$refs.compareContainer;
      if (!container) return;
      const rect = container.getBoundingClientRect();
      const x = (e.touches ? e.touches[0].clientX : e.clientX) - rect.left;
      this.compareSlider = Math.max(0, Math.min(100, (x / rect.width) * 100));
    },
    bestSaving(pdf) {
      const versions = this.versionCache[pdf.id] || [];
      if (versions.length > 0) return Math.max(...versions.map(v => v.compression_ratio || 0));
      return pdf.best_compression_ratio || 0;
    },
    fmtSaving(r) {
      if (r == null || r === 0) return '';
      return r > 0 ? 'saved ' + (r * 100).toFixed(0) + '%' : '+' + (Math.abs(r) * 100).toFixed(0) + '% larger';
    },
    fmtRatio(r) {
      if (r == null || r === 0) return 'No change';
      return r > 0 ? '-' + (r * 100).toFixed(1) + '%' : '+' + (Math.abs(r) * 100).toFixed(1) + '% larger';
    },
    fmtSettings(v) {
      if (v.quality == null) return '';
      return 'Q' + v.quality + ' ' + v.pdf_mode + ' ' + v.pdf_dpi + 'dpi' + (v.pdf_grayscale ? ' gray' : '');
    },
    fmtTarget(bytes) {
      return 'target ' + this.fmtSize(bytes);
    },
    batchSavedText() {
      const ok = (this.batchResults?.results || []).filter(r => r.status === 'ok');
      const saved = ok.reduce((s, r) => s + ((r.original_size || 0) - (r.compressed_size || 0)), 0);
      return saved > 0 ? 'Saved ' + this.fmtSize(saved) + ' total' : 'No size reduction';
    },
    bestVersion(pdf) {
      const versions = this.versionCache[pdf.id] || [];
      if (versions.length === 0) {
        if (!pdf.best_compressed_size) return null;
        return { id: pdf.best_version_id, file_size: pdf.best_compressed_size };
      }
      return versions.reduce((best, v) => (!best || v.file_size < best.file_size) ? v : best, null);
    },
    pdfMeta(pdf) {
      const vc = this._versionGen[pdf.id] || 0;
      const cached = this._pdfMeta[pdf.id];
      if (cached && cached.ts === vc) return cached;
      const meta = { bv: this.bestVersion(pdf), saving: this.bestSaving(pdf), ts: vc };
      this._pdfMeta = { ...this._pdfMeta, [pdf.id]: meta };
      return meta;
    },
    fmtSize(bytes) {
      if (bytes == null) return '-';
      const units = ['B', 'KB', 'MB', 'GB', 'TB'];
      let v = bytes, u = 0;
      while (v >= 1000 && u < 4) { v /= 1000; u++; }
      const fixed = v.toFixed(u === 0 ? 0 : 1);
      return fixed === '1000.0' && u < 4 ? '1.0 ' + units[u + 1] : fixed + ' ' + units[u];
    },
    fmtDate(iso) {
      if (!iso) return '';
      return new Date(iso).toLocaleDateString(undefined, { month: 'short', day: 'numeric', hour: '2-digit', minute: '2-digit' });
    },
    fmtRelative(iso) {
      if (!iso) return '';
      const now = Date.now();
      const then = new Date(iso).getTime();
      const diff = now - then;
      if (diff < 0) return this.fmtDate(iso);
      const seconds = Math.floor(diff / 1000);
      if (seconds < 60) return 'just now';
      const minutes = Math.floor(seconds / 60);
      if (minutes < 60) return `${minutes}m ago`;
      const hours = Math.floor(minutes / 60);
      if (hours < 24) return `${hours}h ago`;
      const days = Math.floor(hours / 24);
      if (days < 7) return `${days}d ago`;
      return this.fmtDate(iso);
    },
    _plural(n, singular, plural) { return n + ' ' + (n === 1 ? singular : (plural || singular + 's')); },
    showToast(msg, type = 'success') {
      this.toast = msg;
      this.toastType = type;
      setTimeout(() => {
        if (this.toast === msg) this.toast = '';
      }, type === 'error' ? 8000 : 3000);
    },
  };
}
