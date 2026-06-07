function app() {
  return {
    pdfs: [],
    versionCache: {},
    versionLoading: {},
    _pdfMeta: {},
    stats: null,
    search: '',
    sortBy: 'date',
    filterTab: 'All',
    presets: ['200KB', '500KB', '1MB', '2MB', '5MB'],
    expanded: null,
    loading: true,
    apiError: false,
    batchCompressing: false,
    showBatchCompress: false,
    batchForm: { quality: 82, target_size: '', pdf_mode: 'auto', pdf_dpi: 120, pdf_grayscale: false, strip_metadata: true, label: '' },
    selectMode: false,
    selectedPdfs: {},
    // Upload
    showUpload: false,
    uploadFiles: [],
    uploading: false,
    uploadProgress: 0,
    uploadStatus: '',
    dragOver: false,
    uploadForm: { quality: 82, target_size: '', pdf_mode: 'auto', pdf_dpi: 120, pdf_grayscale: false, strip_metadata: true, notes: '' },
    // Compress
    showCompress: false,
    compressTarget: null,
    compressing: false,
    compressForm: { quality: 82, target_size: '500KB', pdf_mode: 'auto', pdf_dpi: 120, pdf_grayscale: false, strip_metadata: true, label: '' },
    // Notes
    batchTargetPdfs: [],
    showNotes: false,
    notesTarget: null,
    notesText: '',
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
    _defaults(extra = {}) { return { quality: 82, target_size: '', pdf_mode: 'auto', pdf_dpi: 120, pdf_grayscale: false, strip_metadata: true, ...extra }; },
    _buildCompressFd(form, extras) {
      const fd = new FormData();
      for (const [k, v] of Object.entries(form)) { if (v !== '' && v != null) fd.append(k, v); }
      if (extras) Object.entries(extras).forEach(([k, v]) => fd.append(k, v));
      return fd;
    },
    _validTargetSize(v) {
      if (!v || !v.trim()) return '';
      return /^\s*\d+(\.\d+)?\s*[kmgt]?\s*b?\s*$/i.test(v) ? '' : 'Invalid target size (e.g. 500KB, 500 KB, 2MB)';
    },
    _validateForm(form) {
      if (!(form.quality >= 1 && form.quality <= 95)) return 'Quality must be between 1 and 95';
      if (!(form.pdf_dpi >= 36 && form.pdf_dpi <= 300)) return 'DPI must be between 36 and 300';
      return this._validTargetSize(form.target_size);
    },
    get uploadValidation() { return this._validateForm(this.uploadForm); },
    get compressValidation() { return this._validateForm(this.compressForm); },
    get batchCompressValidation() { return this._validateForm(this.batchForm); },
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
      this.loading = true;
      this.apiError = false;
      try {
        const pdfsRes = await fetch('/api/pdfs');
        if (!pdfsRes.ok) throw new Error('Failed to load PDFs');
        this.pdfs = await pdfsRes.json();
        fetch('/api/stats').then(r => r.ok ? r.json() : null).then(d => { if (d) this.stats = d; }).catch(() => {});
      } catch (e) {
        this.apiError = true;
        this.showToast('Failed to load library: ' + e.message, 'error');
      }
      this.loading = false;
    },
    async loadVersions(pdfId) {
      this.versionLoading[pdfId] = true;
      try {
        const res = await fetch(`/api/pdfs/${pdfId}/versions`);
        if (!res.ok) throw new Error('Failed to load versions');
        this.versionCache[pdfId] = await res.json();
      } catch (e) {
        this.showToast('Failed to load versions: ' + e.message, 'error');
      }
      this.versionLoading[pdfId] = false;
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
      const all = Object.fromEntries(this.filteredPdfs.map(p => [p.id, true]));
      this.selectedPdfs = Object.keys(this.selectedPdfs).length === this.filteredPdfs.length ? {} : all;
    },
    async batchDelete() {
      const ids = Object.keys(this.selectedPdfs);
      if (!confirm(`Delete ${ids.length} PDF(s) and all their versions?`)) return;
      try {
        const res = await fetch('/api/pdfs/batch-delete', {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({ pdf_ids: ids }),
        });
        if (!res.ok) throw new Error('Batch delete failed');
        const data = await res.json();
        this.showToast(`Deleted ${data.deleted} PDFs`);
        if (ids.includes(this.expanded)) this.expanded = null;
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
      const bad = [nonPdfCount > 0 && 'non-PDF', tooLargeCount > 0 && 'too large'].filter(Boolean);
      if (bad.length) this.showToast(`Skipped ${bad.join(', ')} file(s)${!valid.length ? ' — none to upload' : ''}`, valid.length ? 'success' : 'error');
      const existing = new Set(this.uploadFiles.map(f => f.name));
      const newFiles = valid.filter(f => !existing.has(f.name));
      this.uploadFiles = [...this.uploadFiles, ...newFiles];
      if (newFiles.length > 0 && !bad.length) this.showToast(`Added ${newFiles.length} PDF(s) to upload queue`);
    },
    async doUpload() {
      if (!this.uploadFiles.length) return;
      this.uploading = true;
      this.uploadProgress = 0;
      this.uploadStatus = 'Uploading...';
      let succeeded = 0; const errors = []; const warnings = []; let uploadedPdf = null;
      const { notes, ...formFields } = this.uploadForm;
      for (let i = 0; i < this.uploadFiles.length; i++) {
        const fd = this._buildCompressFd(formFields, { file: this.uploadFiles[i], notes });
        this.uploadStatus = `Uploading ${i + 1}/${this.uploadFiles.length}...`;
        try {
          const data = await new Promise((resolve, reject) => {
            const xhr = new XMLHttpRequest();
            this._currentXhr = xhr;
            xhr.open('POST', '/api/pdfs/upload');
            xhr.upload.onprogress = (e) => {
              if (e.lengthComputable) {
                const filePct = e.loaded / e.total;
                this.uploadProgress = Math.round(((i + filePct) / this.uploadFiles.length) * 100);
                if (filePct >= 1) this.uploadStatus = `Processing ${i + 1}/${this.uploadFiles.length}...`;
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
          if (data.warning) warnings.push(data.warning);
          uploadedPdf = data; succeeded++;
        } catch (e) {
          if (!this.uploading) break;
          errors.push(`${this.uploadFiles[i].name}: ${e.message}`);
        }
        if (!this.uploading) break;
      }
      if (succeeded === 0 && !this.uploading) {
        this.showUpload = false; this.uploadFiles = []; this.showToast('Upload cancelled', 'error'); return;
      }
      this.uploadProgress = 100; this.uploading = false;
      if (succeeded === 0 && errors.length > 0) {
        this.showToast(`Upload failed: ${errors.join('; ')}`, 'error');
        return;
      }
      this.showUpload = false; this.uploadFiles = [];
      this.uploadForm = this._defaults({ notes: '' });
      const parts = [`Uploaded ${succeeded} PDF(s)`];
      if (warnings.length) parts.push(`${warnings.length} warning(s): ${warnings.join('; ')}`);
      if (errors.length) parts.push(`${errors.length} failed: ${errors.join('; ')}`);
      this.showToast(parts.join(', '), errors.length ? 'error' : 'success');
      this.loadLibrary();
      this.compressPdf(uploadedPdf);
    },
    cancelUpload() {
      this.uploading = false;
      if (this._currentXhr) { this._currentXhr.abort(); this._currentXhr = null; }
    },
    batchCompress() {
      this.batchForm = this._defaults({ label: '' });
      this.batchTargetPdfs = this.filteredPdfs;
      this.showBatchCompress = true;
    },
    async doBatchCompress() {
      this.batchCompressing = true;
      try {
        const fd = this._buildCompressFd(this.batchForm);
        if (this.batchTargetPdfs.length < this.pdfs.length) {
          fd.append('pdf_ids', this.batchTargetPdfs.map(p => p.id).join(','));
        }
        const res = await fetch('/api/pdfs/batch-compress', { method: 'POST', body: fd });
        const data = await res.json().catch(() => ({}));
        if (!res.ok) throw new Error(data.detail || 'Batch compress failed');
        const errors = (data.results || []).filter(r => r.status === 'error');
        const ok = (data.results || []).filter(r => r.status === 'ok');
        const saved = ok.reduce((s, r) => s + ((r.original_size || 0) - (r.compressed_size || 0)), 0);
        const savedStr = saved > 0 ? ', saved ' + this.fmtSize(saved) : '';
        if (errors.length > 0) {
          this.showToast(`Compressed ${data.compressed}${savedStr}, ${errors.length} failed: ${errors.map(e => e.filename + ' (' + (e.error || 'unknown') + ')').join('; ')}`, 'error');
        } else {
          this.showToast(`Compressed ${data.compressed} PDF(s)${savedStr}`);
        }
        this.loadLibrary();
        this.showBatchCompress = false;
      } catch (e) {
        this.showToast('Batch compress failed: ' + e.message, 'error');
      }
      this.batchCompressing = false;
    },
    compressPdf(pdf) {
      this.compressTarget = pdf;
      const target = pdf.file_size ? Math.max(10000, Math.round(pdf.file_size * 0.5)) : 500000;
      this.compressForm = this._defaults({ target_size: this.fmtSize(target), label: '' });
      this.showCompress = true;
    },
    reuseSettings(pdf, version) {
      this.compressTarget = pdf;
      this.compressForm = {
        quality: version.quality,
        target_size: version.target_bytes ? this.fmtSize(version.target_bytes) : '',
        pdf_mode: version.pdf_mode,
        pdf_dpi: version.pdf_dpi,
        pdf_grayscale: version.pdf_grayscale,
        strip_metadata: version.strip_metadata !== false,
        label: '',
      };
      this.showCompress = true;
    },
    async doCompress() {
      if (!this.compressTarget) return;
      this.compressing = true;
      try {
        const fd = this._buildCompressFd(this.compressForm);
        const res = await fetch(`/api/pdfs/${this.compressTarget.id}/compress`, { method: 'POST', body: fd });
        const ver = await res.json().catch(() => ({}));
        if (!res.ok) throw new Error(ver.detail || 'Compression failed');
        const ratio = ver.compression_ratio;
        const size = this.fmtSize(ver.file_size);
        const msg = ratio > 0 ? `Compressed: -${(ratio * 100).toFixed(1)}% (${size})` : ratio < 0 ? `Larger: +${(Math.abs(ratio) * 100).toFixed(1)}% → ${size}` : `No change → ${size}`;
        this.showToast(msg);
        this.loadVersions(this.compressTarget.id);
        this.loadLibrary();
        this.showCompress = false;
      } catch (e) {
        this.showToast('Compression failed: ' + e.message, 'error');
      }
      this.compressing = false;
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
      this.compareTotalPages = pdf.page_count || 1;
      this.compareSlider = 50;
      this._resetCompareLoading();
      this.showCompare = true;
    },
    comparePrev() { if (this.comparePage > 0) { this.comparePage--; this._resetCompareLoading(); } },
    compareNext() { if (this.comparePage < this.compareTotalPages - 1) { this.comparePage++; this._resetCompareLoading(); } },
    _resetCompareLoading() { this.compareLoading = true; this.compareError = ''; this._compareLoaded = 0; this._compareGen++; },
    _onCompareLoad(gen) { if (gen !== this._compareGen) return; if (++this._compareLoaded >= 2) this.compareLoading = false; },
    _onCompareError() { this.compareLoading = false; this.compareError = 'Failed to load preview'; },
    compareOriginalUrl() {
      return `/api/preview/original/${this.comparePdf.id}/${this.comparePage}`;
    },
    compareVersionUrl() {
      return `/api/preview/version/${this.compareVersion.id}/${this.comparePage}`;
    },
    _onCompareMouseDown(e) {
      this._compareDragging = true;
      this._updateCompareSlider(e);
      this._onMouseMove = (ev) => { if (this._compareDragging) this._updateCompareSlider(ev); };
      this._onMouseUp = () => { this._compareDragging = false; window.removeEventListener('mousemove', this._onMouseMove); window.removeEventListener('mouseup', this._onMouseUp); window.removeEventListener('touchmove', this._onMouseMove); window.removeEventListener('touchend', this._onMouseUp); };
      window.addEventListener('mousemove', this._onMouseMove);
      window.addEventListener('mouseup', this._onMouseUp);
      window.addEventListener('touchmove', this._onMouseMove, { passive: false });
      window.addEventListener('touchend', this._onMouseUp);
    },
    _onCompareMouseMove(e) { if (this._compareDragging) this._updateCompareSlider(e); },
    _onCompareMouseUp() { this._compareDragging = false; },
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
    bestVersion(pdf) {
      const versions = this.versionCache[pdf.id] || [];
      if (versions.length === 0) {
        if (!pdf.best_compressed_size) return null;
        return { id: pdf.best_version_id, file_size: pdf.best_compressed_size };
      }
      return versions.reduce((best, v) => (!best || v.file_size < best.file_size) ? v : best, null);
    },
    pdfMeta(pdf) {
      const vc = (this.versionCache[pdf.id] || []).length;
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
    showToast(msg, type = 'success') {
      this.toast = msg; this.toastType = type;
      setTimeout(() => { if (this.toast === msg) this.toast = ''; }, type === 'error' ? 8000 : 3000);
    },
  };
}
