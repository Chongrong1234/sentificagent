;;; init-example.el --- Scientific Agent elfeed setup example

(use-package elfeed
  :ensure t)

(use-package elfeed-score
  :ensure t
  :after elfeed
  :config
  (setq elfeed-score-serde-score-file
        "/home/lichongrong/桌面/scientific_agent/tools/elfeed/elfeed-score.scoring")
  (elfeed-score-enable))

(add-to-list 'load-path "/home/lichongrong/桌面/scientific_agent/tools/elfeed")
(require 'scientific-agent-elfeed)

(setq scientific-agent-root "/home/lichongrong/桌面/scientific_agent")
(setq scientific-agent-score-threshold 80)
(setq scientific-agent-max-entries-per-run 12)
(scientific-agent-install-hooks)

;; Optional key for capturing the current elfeed-show entry into org.
;; (define-key elfeed-show-mode-map (kbd "C-c c") #'scientific-agent-org-capture-entry)
