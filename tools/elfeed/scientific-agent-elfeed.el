;;; scientific-agent-elfeed.el --- elfeed workflow for Scientific Agent -*- lexical-binding: t; -*-

;;; Commentary:
;; This file follows the workflow described in the reference article:
;; elfeed + elfeed-score discover and rank entries, high-score or manually tagged
;; entries are exported to article-summarizer, summaries are written back to
;; elfeed-entry metadata, and org-capture can schedule follow-up reading.

;;; Code:

(require 'cl-lib)
(require 'json)
(require 'subr-x)
(require 'url-util)

(require 'elfeed)
(require 'elfeed-search)
(require 'elfeed-db)
(require 'elfeed-score nil t)
(require 'org-capture nil t)

(defgroup scientific-agent-elfeed nil
  "Scientific Agent elfeed automation."
  :group 'elfeed)

(defcustom scientific-agent-root
  (expand-file-name "../.." (file-name-directory (or load-file-name buffer-file-name)))
  "Project root."
  :type 'directory)

(defcustom scientific-agent-score-threshold 80
  "Minimum elfeed-score value required for automatic summarization."
  :type 'integer)

(defcustom scientific-agent-summary-tag 'to-summarize
  "Manual tag that forces summarization."
  :type 'symbol)

(defcustom scientific-agent-summarized-tag 'summarized
  "Tag added after a summary is written back."
  :type 'symbol)

(defcustom scientific-agent-failed-tag 'summary-failed
  "Tag added when article summarization fails."
  :type 'symbol)

(defcustom scientific-agent-max-entries-per-run 12
  "Maximum entries to summarize after one elfeed update."
  :type 'integer)

(defcustom scientific-agent-article-summarizer-dir
  (expand-file-name "tools/article-summarizer" scientific-agent-root)
  "Directory containing the Node article-summarizer."
  :type 'directory)

(defcustom scientific-agent-output-dir
  (expand-file-name "data/library/elfeed" scientific-agent-root)
  "Output directory for elfeed automation artifacts."
  :type 'directory)

(defun scientific-agent--feed-lines-from-yaml ()
  "Read feed URLs from configs/attention_feeds.yaml without a YAML parser."
  (let* ((path (expand-file-name "configs/attention_feeds.yaml" scientific-agent-root))
         (lines (when (file-exists-p path)
                  (split-string (with-temp-buffer
                                  (insert-file-contents path)
                                  (buffer-string))
                                "\n" t))))
    (cl-loop for line in lines
             when (string-match-p "\\`[[:space:]]*-[[:space:]]+https?://" line)
             collect (string-trim (replace-regexp-in-string "\\`[[:space:]]*-[[:space:]]+" "" line)))))

(defun scientific-agent-configure-feeds ()
  "Configure `elfeed-feeds' from project feed file."
  (interactive)
  (setq elfeed-feeds
        (mapcar (lambda (url) (list url 'scientific-agent))
                (scientific-agent--feed-lines-from-yaml)))
  elfeed-feeds)

(defun scientific-agent--score (entry)
  "Return elfeed-score score for ENTRY, or 0 if unavailable."
  (cond
   ((fboundp 'elfeed-score-scoring-get-score-from-entry)
    (or (elfeed-score-scoring-get-score-from-entry entry) 0))
   ((fboundp 'elfeed-score-get-score-from-entry)
    (or (elfeed-score-get-score-from-entry entry) 0))
   (t 0)))

(defun scientific-agent-entry-needs-summary-p (entry)
  "Return non-nil if ENTRY should be summarized."
  (let ((tags (elfeed-entry-tags entry)))
    (and (not (memq scientific-agent-summarized-tag tags))
         (or (memq scientific-agent-summary-tag tags)
             (> (scientific-agent--score entry) scientific-agent-score-threshold)))))

(defun scientific-agent--entry-url (entry)
  "Return primary URL for ENTRY."
  (or (elfeed-entry-link entry)
      (when-let ((id (elfeed-entry-id entry)))
        (cdr-safe id))
      ""))

(defun scientific-agent--candidate-entries ()
  "Return candidate entries sorted by elfeed-score."
  (let (entries)
    (with-elfeed-db-visit (entry _feed)
      (when (scientific-agent-entry-needs-summary-p entry)
        (push entry entries)))
    (seq-take
     (sort entries
           (lambda (a b)
             (> (scientific-agent--score a) (scientific-agent--score b))))
     scientific-agent-max-entries-per-run)))

(defun scientific-agent--timestamp ()
  "Return compact timestamp."
  (format-time-string "%Y%m%dT%H%M%SZ" (current-time) t))

(defun scientific-agent--write-lines (path lines)
  "Write LINES to PATH."
  (make-directory (file-name-directory path) t)
  (with-temp-file path
    (insert (string-join lines "\n"))
    (insert "\n")))

(defun scientific-agent--call-summarizer (urls output-file)
  "Call Node article-summarizer for URLS, writing OUTPUT-FILE."
  (let ((input-file (expand-file-name (format "%s-urls.txt" (scientific-agent--timestamp))
                                      scientific-agent-output-dir))
        (default-directory scientific-agent-article-summarizer-dir))
    (scientific-agent--write-lines input-file urls)
    (unless (file-exists-p (expand-file-name "node_modules" scientific-agent-article-summarizer-dir))
      (error "Missing node_modules in %s. Run npm install first." scientific-agent-article-summarizer-dir))
    (let ((exit-code
           (call-process "npm" nil "*scientific-agent-article-summarizer*" t
                         "run" "summarize" "--" input-file output-file)))
      (unless (equal exit-code 0)
        (error "article-summarizer failed with exit code %s" exit-code)))))

(defun scientific-agent--read-json-array (path)
  "Read JSON array from PATH."
  (let ((json-array-type 'list)
        (json-object-type 'alist)
        (json-key-type 'symbol))
    (json-read-file path)))

(defun scientific-agent--result-summary (result)
  "Extract summary string from RESULT."
  (let ((summary (alist-get 'summary result)))
    (cond
     ((stringp summary) summary)
     ((null summary) "")
     (t (json-encode summary)))))

(defun scientific-agent--write-summary-to-entry (entry result)
  "Write RESULT summary back to ENTRY metadata."
  (setf (elfeed-meta entry :summary) (scientific-agent--result-summary result))
  (setf (elfeed-meta entry :article-title) (or (alist-get 'title result) ""))
  (setf (elfeed-meta entry :article-excerpt) (or (alist-get 'excerpt result) ""))
  (setf (elfeed-meta entry :article-length) (or (alist-get 'length result) 0))
  (elfeed-tag entry scientific-agent-summarized-tag)
  (elfeed-untag entry scientific-agent-summary-tag)
  (elfeed-db-save))

(defun scientific-agent-summarize-candidates ()
  "Summarize high-priority elfeed entries and write results to metadata."
  (interactive)
  (let* ((entries (scientific-agent--candidate-entries))
         (urls (mapcar #'scientific-agent--entry-url entries))
         (timestamp (scientific-agent--timestamp))
         (output-file (expand-file-name (format "%s-summaries.json" timestamp)
                                        scientific-agent-output-dir)))
    (if (null entries)
        (message "No elfeed entries need summarization.")
      (condition-case err
          (progn
            (scientific-agent--call-summarizer urls output-file)
            (cl-loop for entry in entries
                     for result in (scientific-agent--read-json-array output-file)
                     do (scientific-agent--write-summary-to-entry entry result))
            (scientific-agent-export-org-schedule entries timestamp)
            (message "Scientific Agent summarized %d entries." (length entries)))
        (error
         (dolist (entry entries)
           (elfeed-tag entry scientific-agent-failed-tag))
         (elfeed-db-save)
         (signal (car err) (cdr err)))))))

(defun scientific-agent-after-elfeed-update (&rest _args)
  "Hook function run after elfeed update."
  (run-at-time 1 nil #'scientific-agent-summarize-candidates))

(defun scientific-agent-install-hooks ()
  "Install elfeed automation hooks."
  (interactive)
  (scientific-agent-configure-feeds)
  (add-hook 'elfeed-update-hooks #'scientific-agent-after-elfeed-update)
  (add-hook 'elfeed-update-init-hooks #'scientific-agent-after-elfeed-update))

(defun scientific-agent--org-entry (entry)
  "Return Org TODO text for ENTRY."
  (let ((title (elfeed-entry-title entry))
        (url (scientific-agent--entry-url entry))
        (score (scientific-agent--score entry))
        (summary (or (elfeed-meta entry :summary) "")))
    (format "* TODO 阅读：%s
SCHEDULED: <%s>
:PROPERTIES:
:URL: %s
:SCORE: %s
:END:

- 摘要：%s
"
            title
            (format-time-string "%Y-%m-%d %a" (time-add (current-time) (days-to-time 3)))
            url
            score
            (replace-regexp-in-string "[\r\n]+" " " summary))))

(defun scientific-agent-export-org-schedule (&optional entries timestamp)
  "Export summarized ENTRIES to an Org schedule file."
  (interactive)
  (let* ((resolved-entries (or entries (scientific-agent--candidate-entries)))
         (resolved-timestamp (or timestamp (scientific-agent--timestamp)))
         (path (expand-file-name (format "%s-schedule.org" resolved-timestamp)
                                 scientific-agent-output-dir)))
    (make-directory (file-name-directory path) t)
    (with-temp-file path
      (insert "#+TITLE: Scientific Agent Elfeed Schedule\n\n")
      (dolist (entry resolved-entries)
        (insert (scientific-agent--org-entry entry))
        (insert "\n")))
    path))

(defun scientific-agent-org-capture-entry ()
  "Capture current elfeed entry with its summary."
  (interactive)
  (unless (derived-mode-p 'elfeed-show-mode)
    (error "Run from elfeed-show-mode"))
  (let ((entry elfeed-show-entry))
    (org-capture-string (scientific-agent--org-entry entry) "t")))

(provide 'scientific-agent-elfeed)

;;; scientific-agent-elfeed.el ends here
