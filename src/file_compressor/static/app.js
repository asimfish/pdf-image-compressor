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
    _libraryGen: 0,
    stats: null,
    _maxUploadBytes: 500 * 1024 * 1024,
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
    batchCompressError: '',
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
    compressError: '',
    _compressGen: 0,
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
    compareRetryable: true,
    _compareLoaded: 0,
    _compareErrors: 0,
    _compareRetries: 0,
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
        const val = typeof v === 'string' ? v.trim() : v;
        if (val !== '' && val != null && !(typeof val === 'number' && isNaN(val))) fd.append(k, val);
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
      if (!/^\s*\d+(?:\.\d+)?\s*(?:\s*[kmgt]?b?)?\s*$/i.test(v)) return 'Enter a size (e.g. 500KB, 2MB, 1.5M)';
      const num = parseFloat(v);
      if (num === 0) return 'Target size must be greater than zero';
      const unit = v.trim().replace(/[\d.\s]/g, '').toLowerCase();
      const multipliers = { k: 1000, m: 1000000, g: 1000000000, t: 1000000000000 };
      const bytes = num * (multipliers[unit.charAt(0)] || 1);
      if (bytes < 1) return 'Target size must be at least 1 byte';
      if (!unit && bytes < 1000) return 'Include a unit (e.g. 500KB, 2MB) — bare numbers are treated as bytes';
      return '';
    },
    _validateForm(form) {
      if (isNaN(form.quality)) return 'Quality is required';
      if (!(form.quality >= 1 && form.quality <= 95)) return 'Quality must be between 1 and 95';
      if (isNaN(form.pdf_dpi)) return 'DPI is required';
      if (!(form.pdf_dpi >= 36 && form.pdf_dpi <= 300)) return 'DPI must be between 36 and 300';
      return this._validTargetSize(form.target_size);
    },
    get uploadValidation() {
      if (this.uploadFiles.length === 0) return 'Select PDF files to upload';
      return this._validateForm(this.uploadForm);
    },
    get compressValidation() { return this._validateForm(this.compressForm); },
    get batchCompressValidation() { return this._validateForm(this.batchForm); },
    get allFilteredSelected() {
      const fids = new Set(this.filteredPdfs.map(p => p.id));
      const sids = new Set(Object.keys(this.selectedPdfs));
      return fids.size > 0 && fids.size === sids.size && [...fids].every(id => sids.has(id));
    },
    get anyModalOpen() {
      return this.showUpload || this.showCompress || this.showBatchCompress || this.showNotes || this.showCompare || this.showShortcuts;
    },
    get batchCompressDisabled() {
      return this.batchCompressing || (this.selectMode && (Object.keys(this.selectedPdfs).length === 0 || Object.keys(this.selectedPdfs).length > 50)) || (!this.selectMode && (this.filteredPdfs.length === 0 || this.filteredPdfs.length > 50));
    },
    get filteredPdfs() {
      const key = `${this.search}|${this.filterTab}|${this.sortBy}`;
      if (this._filteredKey === key && this._filteredCache) return this._filteredCache;
      this._filteredKey = key;
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
      this._filteredCache = sorted;
      return sorted;
    },
    get uncompressedCount() {
      return this.pdfs.filter(p => (p.version_count || 0) === 0).length;
    },
    get compressedCount() {
      return this.pdfs.filter(p => (p.version_count || 0) > 0).length;
    },
    async loadLibrary() {
      const gen = ++this._libraryGen;
      const isInitial = !this._libraryLoaded;
      this.loading = isInitial;
      this.apiError = false;
      try {
        const pdfsRes = await fetch('/api/pdfs');
        if (!pdfsRes.ok) throw new Error('Failed to load PDFs');
        if (gen !== this._libraryGen) return;
        const newPdfs = await pdfsRes.json();
        if (gen !== this._libraryGen) return;
        this.pdfs = newPdfs;
        this._filteredCache = null;
        this._pdfMeta = {};
        if (this.expanded) {
          const expandedId = this.expanded;
          const { [expandedId]: _vc, ...restVersions } = this.versionCache;
          this.versionCache = restVersions;
          const { [expandedId]: _vl, ...restLoading } = this.versionLoading;
          this.versionLoading = restLoading;
          const { [expandedId]: _vg, ...restGen } = this._versionGen;
          this._versionGen = restGen;
        } else {
          this.versionCache = {};
          this.versionLoading = {};
          this._versionGen = {};
        }
        fetch('/api/stats').then(r => r.ok ? r.json() : null).then(d => { if (d && gen === this._libraryGen) this.stats = d; }).catch(() => {});
        fetch('/api/config').then(r => r.ok ? r.json() : null).then(d => { if (d && d.max_upload_bytes && gen === this._libraryGen) this._maxUploadBytes = d.max_upload_bytes; }).catch(() => {});
      } catch (e) {
        if (gen !== this._libraryGen) return;
        this.apiError = true;
        this.showToast('Failed to load library: ' + e.message, 'error');
      }
      if (gen === this._libraryGen) {
        this._libraryLoaded = true;
        this.loading = false;
        if (this.expanded && this.versionCache[this.expanded] === undefined) {
          this.loadVersions(this.expanded);
        }
      }
    },
    async loadVersions(pdfId) {
      const gen = (this._versionGen[pdfId] || 0) + 1;
      this._versionGen[pdfId] = gen;
      this.versionLoading[pdfId] = true;
      const { [pdfId]: _pm, ...restMeta } = this._pdfMeta;
      this._pdfMeta = restMeta;
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
        const data = await res.json().catch(() => ({}));
        if (!res.ok) throw new Error(data.detail || 'Batch delete failed');
        this.showToast(`Deleted ${data.deleted} PDFs`);
        ids.forEach(id => this._evictPdfCache(id));
        if (ids.includes(this.expanded)) this.expanded = null;
        if (this.showCompress && ids.includes(this.compressTarget?.id)) this.closeCompress();
        if (this.showNotes && ids.includes(this.notesTarget?.id)) { this.showNotes = false; this.notesTarget = null; }
        if (this.showCompare && ids.includes(this.comparePdf?.id)) { clearTimeout(this._compareTimeout); this._cleanupCompareListeners(); this.showCompare = false; }
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
    _resetDragState() {
      this._globalDragCounter = 0;
      this.globalDragActive = false;
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
      const files = [...e.dataTransfer.files];
      if (!files.length) { this.globalDragActive = false; return; }
      if (this.selectMode || this.showUpload || this.showCompress || this.showBatchCompress || this.showNotes || this.showCompare || this.showShortcuts) {
        this.showToast(this.selectMode ? 'Exit selection mode before dropping files' : 'Close the current dialog before dropping files', 'error');
        setTimeout(() => { this.globalDragActive = false; }, 600);
        return;
      }
      this.globalDragActive = false;
      const prevCount = this.uploadFiles.length;
      this._selectFiles(files);
      if (this.uploadFiles.length > prevCount) this.showUpload = true;
    },
    handleFiles(files) {
      this._selectFiles([...files]);
    },
    _selectFiles(all) {
      const MAX = this._maxUploadBytes;
      const pdfs = all.filter(f => f.name.toLowerCase().endsWith('.pdf'));
      if (!pdfs.length) { this.showToast('Only PDF files are supported', 'error'); return; }
      const valid = pdfs.filter(f => f.size <= MAX);
      const nonPdfNames = all.filter(f => !f.name.toLowerCase().endsWith('.pdf')).map(f => f.name);
      const tooLargeNames = pdfs.filter(f => f.size > MAX).map(f => f.name);
      const existing = new Set(this.uploadFiles.map(f => `${f.name}:${f.size}:${f.lastModified}`));
      const newFiles = valid.filter(f => !existing.has(`${f.name}:${f.size}:${f.lastModified}`));
      const dupes = valid.length - newFiles.length;
      this.uploadFiles = [...this.uploadFiles, ...newFiles];
      // Build a single toast message with skipped file names
      const parts = [];
      if (nonPdfNames.length > 0) {
        const names = nonPdfNames.slice(0, 3).join(', ') + (nonPdfNames.length > 3 ? ` +${nonPdfNames.length - 3}` : '');
        parts.push(`Skipped ${nonPdfNames.length} non-PDF (${names})`);
      }
      if (tooLargeNames.length > 0) {
        const names = tooLargeNames.slice(0, 3).join(', ') + (tooLargeNames.length > 3 ? ` +${tooLargeNames.length - 3}` : '');
        parts.push(`Skipped ${tooLargeNames.length} over limit (${names})`);
      }
      if (newFiles.length > 0) parts.push(`Added ${this._plural(newFiles.length, 'PDF')}`);
      if (dupes > 0) parts.push(this._plural(dupes, 'duplicate'));
      if (!parts.length) { this.showToast('No new files to add', 'error'); return; }
      const isError = newFiles.length === 0 && dupes === 0;
      this.showToast(parts.join(', '), isError ? 'error' : newFiles.length === 0 ? 'warning' : 'success');
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
        xhr.timeout = 600000;
        xhr.ontimeout = () => { this._currentXhr = null; reject(new Error('Server timed out')); };
        xhr.send(fd);
      });
    },
    async doUpload() {
      if (!this.uploadFiles.length) return;
      this._uploadCancelled = false;
      this.uploading = true;
      this.uploadProgress = 0;
      this.uploadStatus = 'Uploading...';
      let succeeded = 0; const succeededIndices = new Set(); const errors = []; const warnings = []; const compressResults = []; let uploadedPdf = null; const totalFiles = this.uploadFiles.length;
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
          uploadedPdf = data; succeeded++; succeededIndices.add(i);
        } catch (e) {
          if (!this.uploading) break;
          errors.push(`${this.uploadFiles[i].name}: ${e.message}`);
        }
        if (!this.uploading) break;
      }
      if (succeeded === 0 && this._uploadCancelled) {
        const msg = errors.length > 0 ? `Upload cancelled (${errors.length} failed)` : 'Upload cancelled';
        this.showUpload = false; this.uploadFiles = []; this.showToast(msg, errors.length ? 'error' : 'warning'); return;
      }
      this.uploading = false;
      if (succeeded === 0 && errors.length > 0) {
        this.uploadProgress = 0;
        this.showToast(`Upload failed: ${errors.join('; ')}`, 'error');
        return;
      }
      if (this._uploadCancelled && succeeded > 0) {
        this.uploadFiles = this.uploadFiles.filter((_, idx) => !succeededIndices.has(idx));
        if (this.uploadFiles.length === 0) {
          this.uploadProgress = 100;
          this.showUpload = false;
          this.uploadForm = this._defaults({ notes: '' });
          this.showToast(this._buildUploadToast(succeeded, compressResults, warnings, []));
          await this.loadLibrary();
          if (totalFiles === 1 && succeeded === 1 && uploadedPdf) {
            this.compressPdf(uploadedPdf);
          }
          return;
        }
        this.uploadProgress = Math.round((succeeded / totalFiles) * 100);
        this.uploadStatus = `Uploaded ${succeeded}/${totalFiles} PDFs — click Upload to continue with remaining ${this.uploadFiles.length}`;
        this.showToast(`Uploaded ${succeeded}/${totalFiles} PDFs (${this.uploadFiles.length} remaining)`, 'warning');
        await this.loadLibrary();
        return;
      }
      this.showUpload = false; this.uploadFiles = [];
      this.uploadForm = this._defaults({ notes: '' });
      this.showToast(this._buildUploadToast(succeeded, compressResults, warnings, errors), errors.length ? 'error' : 'success');
      await this.loadLibrary();
      if (totalFiles === 1 && succeeded === 1 && uploadedPdf) {
        this.compressPdf(uploadedPdf);
      }
    },
    _buildUploadToast(succeeded, compressResults, warnings, errors) {
      const totalSaved = compressResults.reduce((s, r) => s + (r.original - r.compressed), 0);
      const parts = [`Uploaded ${this._plural(succeeded, 'PDF')}`];
      if (totalSaved > 0) parts.push(`saved ${this.fmtSize(totalSaved)}`);
      if (warnings.length) parts.push(warnings.join('; '));
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
      const selected = this.selectMode ? this.pdfs.filter(p => this.selectedPdfs[p.id]) : [];
      const targets = selected.length > 0 ? selected : this.filteredPdfs;
      if (targets.length > 50) {
        this.showToast(`Select at most 50 PDFs for batch compress (currently ${targets.length})`, 'error');
        return;
      }
      if (targets.length === 0) {
        this.showToast('No PDFs to compress', 'error');
        return;
      }
      this.batchForm = this._defaults({ label: '' });
      this.batchTargetPdfs = targets;
      this._batchTotalAtOpen = this.pdfs.length;
      this._batchIsSubset = targets.length < this.pdfs.length || this.selectMode;
      this.batchResults = null;
      this._resetDragState();
      this.showBatchCompress = true;
    },
    async doBatchCompress() {
      this.batchCompressing = true;
      this.batchResults = null;
      this.batchCompressError = '';
      this._batchAbort = new AbortController();
      try {
        const fd = this._buildCompressFd(this.batchForm);
        if (this._batchIsSubset) {
          fd.append('pdf_ids', this.batchTargetPdfs.map(p => p.id).join(','));
        }
        const res = await fetch('/api/pdfs/batch-compress', { method: 'POST', body: fd, signal: this._batchAbort.signal });
        const data = await res.json().catch(() => ({}));
        if (!res.ok) throw new Error(data.detail || 'Batch compress failed');
        this.batchResults = data;
        await this.loadLibrary();
      } catch (e) {
        if (e.name === 'AbortError') return;
        this.batchCompressError = e.message;
        this.showToast('Batch compress failed: ' + e.message, 'error');
      } finally {
        this._batchAbort = null;
        this.batchCompressing = false;
      }
    },
    cancelBatchCompress() {
      if (this._batchAbort) this._batchAbort.abort();
      this.showBatchCompress = false;
      this.batchResults = null;
      this.loading = true;
      this.loadLibrary();
    },
    closeBatchResults() {
      this.batchResults = null;
      this.batchCompressError = '';
      this.showBatchCompress = false;
    },
    compressPdf(pdf) {
      this.compressTarget = pdf;
      this.compressResult = null;
      const base = pdf.file_size;
      const target = base ? Math.min(base, Math.max(10000, Math.round(base * 0.8))) : 500000;
      this.compressForm = this._defaults({ target_size: this.fmtSize(target), label: '' });
      this._resetDragState();
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
      this._resetDragState();
      this.showCompress = true;
    },
    async doCompress() {
      if (this.compressing || !this.compressTarget) return;
      const gen = ++this._compressGen;
      const targetId = this.compressTarget.id;
      this.compressing = true;
      this.compressResult = null;
      this.compressError = '';
      this._compressAbort = new AbortController();
      try {
        const fd = this._buildCompressFd(this.compressForm);
        const res = await fetch(`/api/pdfs/${targetId}/compress`, { method: 'POST', body: fd, signal: this._compressAbort.signal });
        const ver = await res.json().catch(() => ({}));
        if (!res.ok) throw new Error(ver.detail || 'Compression failed');
        if (gen !== this._compressGen) return;
        this.compressResult = ver;
        await this.loadLibrary();
        if (gen === this._compressGen && this.compressTarget) {
          const fresh = this.pdfs.find(p => p.id === targetId);
          if (fresh) this.compressTarget = fresh;
          await this.loadVersions(targetId);
        }
      } catch (e) {
        if (gen !== this._compressGen) return;
        this.compressError = e.message;
        this.showToast('Compression failed: ' + e.message, 'error');
      } finally {
        this._compressAbort = null;
      }
      if (gen === this._compressGen) this.compressing = false;
    },
    closeCompress() {
      if (this._compressAbort) { this._compressAbort.abort(); this._compressAbort = null; }
      ++this._compressGen;
      this.compressing = false;
      this.compressResult = null;
      this.compressError = '';
      this.showCompress = false;
      this.compressTarget = null;
    },
    editNotes(pdf) {
      this.notesTarget = pdf;
      this.notesText = pdf.notes || '';
      this._resetDragState();
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
        this.notesTarget = null;
        this.loadLibrary();
      } catch (e) {
        this.showToast('Save failed: ' + e.message, 'error');
      }
    },
    async deletePdf(pdf) {
      if (!confirm(`Delete "${pdf.filename}" and all versions?`)) return;
      try {
        const res = await fetch(`/api/pdfs/${pdf.id}`, { method: 'DELETE' });
        const data = await res.json().catch(() => ({}));
        if (!res.ok) throw new Error(data.detail || 'Delete failed');
        if (this.expanded === pdf.id) this.expanded = null;
        if (this.showCompress && this.compressTarget?.id === pdf.id) this.closeCompress();
        if (this.showNotes && this.notesTarget?.id === pdf.id) { this.showNotes = false; this.notesTarget = null; }
        if (this.showCompare && this.comparePdf?.id === pdf.id) { clearTimeout(this._compareTimeout); this._cleanupCompareListeners(); this.showCompare = false; }
        this._evictPdfCache(pdf.id);
        if (this.selectedPdfs[pdf.id]) {
          const n = { ...this.selectedPdfs };
          delete n[pdf.id];
          this.selectedPdfs = n;
        }
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
        const data = await res.json().catch(() => ({}));
        if (!res.ok) throw new Error(data.detail || 'Delete failed');
        if (this.showCompare && this.compareVersion?.id === versionId) { clearTimeout(this._compareTimeout); this._cleanupCompareListeners(); this.showCompare = false; }
        this._evictPdfCache(pdfId);
        this.loadVersions(pdfId);
        this.loadLibrary();
      } catch (e) {
        this.showToast('Delete failed: ' + e.message, 'error');
      }
    },
    // Compare
    openCompare(pdf, version) {
      if ((pdf.page_count || 0) === 0) {
        this.showToast('Cannot compare: original PDF has no readable pages', 'error');
        return;
      }
      this.comparePdf = pdf;
      this.compareVersion = version;
      this.comparePage = 0;
      this.compareTotalPages = Math.max(1, Math.min(pdf.page_count || 1, version.page_count || 1));
      this.compareSlider = 50;
      this._compareRetries = 0;
      this._resetCompareLoading();
      this._resetDragState();
      this.showCompare = true;
    },
    comparePrev() { if (this.comparePage > 0) { this.comparePage--; this._resetCompareLoading(); } },
    compareNext() { if (this.comparePage < this.compareTotalPages - 1) { this.comparePage++; this._resetCompareLoading(); } },
    _resetCompareLoading() {
      clearTimeout(this._compareTimeout);
      this.compareLoading = true;
      this.compareError = '';
      this.compareRetryable = true;
      this._compareLoaded = 0;
      this._compareErrors = 0;
      this._compareGen++;
      const gen = this._compareGen;
      this._compareTimeout = setTimeout(() => {
        if (gen !== this._compareGen || !this.compareLoading) return;
        this.compareLoading = false;
        this._compareRetries++;
        this.compareRetryable = this._compareRetries < 3;
        this.compareError = 'Loading timed out — try again';
      }, 15000);
    },
    _onCompareLoad(e) {
      if (!this.showCompare) return;
      if (+e.target.dataset.gen !== this._compareGen) return;
      if (this.compareTotalPages === 0) return;
      this._compareLoaded++;
      if (this._compareLoaded >= 2) {
        clearTimeout(this._compareTimeout);
        this.compareLoading = false;
        if (this._compareErrors > 0) {
          this._compareRetries++;
          this.compareRetryable = this._compareRetries < 3;
          this.compareError = this._compareRetries >= 3
            ? 'Preview failed after multiple attempts'
            : this._compareErrors >= 2 ? 'Both previews failed to load' : 'One preview failed to load';
        } else {
          this.compareError = '';
        }
      }
    },
    _onCompareError(e) {
      if (!this.showCompare) return;
      if (+e.target.dataset.gen !== this._compareGen) return;
      if (this.compareTotalPages === 0) return;
      e.target.removeAttribute('src');
      this._compareLoaded++;
      this._compareErrors++;
      if (this._compareLoaded >= 2) {
        this.compareLoading = false;
        this._compareRetries++;
        this.compareRetryable = this._compareRetries < 3;
        this.compareError = this._compareRetries >= 3
          ? 'Preview failed after multiple attempts'
          : this._compareErrors >= 2 ? 'Both previews failed to load' : 'One preview failed to load';
      }
    },
    compareOriginalUrl() {
      return `/api/preview/original/${this.comparePdf.id}/${this.comparePage}?_=${this._compareGen}`;
    },
    compareVersionUrl() {
      return `/api/preview/version/${this.compareVersion.id}/${this.comparePage}?v=${this.compareVersion.created_at || ''}&_=${this._compareGen}`;
    },
    _cleanupCompareListeners() {
      clearTimeout(this._compareTimeout);
      this._compareDragging = false;
      this.compareLoading = false;
      this.compareError = '';
      this._compareLoaded = 0;
      this._compareErrors = 0;
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
      if (e.touches) e.preventDefault();
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
      return pdf.best_compression_ratio ?? 0;
    },
    fmtSaving(r) {
      if (r == null || r === 0) return '';
      return r > 0 ? 'saved ' + (r * 100).toFixed(1) + '%' : '+' + (Math.abs(r) * 100).toFixed(1) + '% larger';
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
      const results = this.batchResults?.results || [];
      const ok = results.filter(r => r.status === 'ok');
      const errors = results.filter(r => r.status === 'error');
      const notFound = results.filter(r => r.status === 'not_found');
      if (ok.length === 0 && errors.length > 0) return 'All compressions failed';
      if (ok.length === 0 && notFound.length > 0) return notFound.length + ' PDFs not found (may have been deleted)';
      const saved = ok.reduce((s, r) => s + ((r.original_size || 0) - (r.compressed_size || 0)), 0);
      const parts = [];
      if (saved > 0) parts.push('Saved ' + this.fmtSize(saved) + ' total');
      else if (saved < 0) parts.push('Files grew by ' + this.fmtSize(Math.abs(saved)));
      else parts.push('No size change');
      if (errors.length > 0) parts.push(errors.length + ' failed');
      if (notFound.length > 0) parts.push(notFound.length + ' not found');
      return parts.join(', ');
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
      this._pdfMeta[pdf.id] = meta;
      return meta;
    },
    fmtSize(bytes) {
      if (bytes == null) return '-';
      const units = ['B', 'KB', 'MB', 'GB', 'TB'];
      let v = bytes, u = 0;
      while (v >= 1000 && u < 4) { v /= 1000; u++; }
      if (v >= 999.95 && u < 4) { v /= 1000; u++; }
      return v.toFixed(u === 0 ? 0 : 1) + ' ' + units[u];
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
      clearTimeout(this._toastTimer);
      this.toast = msg;
      this.toastType = type;
      this._toastTimer = setTimeout(() => {
        if (this.toast === msg) this.toast = '';
      }, type === 'error' ? 8000 : type === 'warning' ? 5000 : 3000);
    },
  };
}
