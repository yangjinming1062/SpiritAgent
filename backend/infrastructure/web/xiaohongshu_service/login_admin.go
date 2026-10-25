package main

import (
	"context"
	"crypto/rand"
	"encoding/hex"
	"encoding/json"
	"errors"
	"io"
	"net/http"
	"net/url"
	"os"
	"regexp"
	"strconv"
	"strings"
	"sync"
	"time"

	"github.com/gin-gonic/gin"
	"github.com/go-rod/rod"
	"github.com/go-rod/rod/lib/cdp"
	"github.com/go-rod/rod/lib/proto"
	"github.com/sirupsen/logrus"
	"github.com/xpzouying/xiaohongshu-mcp/browser"
	"github.com/xpzouying/xiaohongshu-mcp/configs"
	"github.com/xpzouying/xiaohongshu-mcp/xiaohongshu"
)

const loginLifetime = 4 * time.Minute

var (
	loginSessionID                           = regexp.MustCompile(`^[A-Za-z0-9_-]{16,96}$`)
	loginCode                                = regexp.MustCompile(`^[0-9]{6}$`)
	sourceID                                 = regexp.MustCompile(`^[A-Za-z0-9_-]{1,96}$`)
	errLoginGone                             = errors.New("login session is unavailable")
	errLoginExpiry                           = errors.New("login session has expired")
	errLoginFailed                           = errors.New("login session failed")
	errLoginSMS                              = errors.New("login session is not awaiting an SMS code")
	errLoginCheck                            = errors.New("login page check failed")
	errLoginInput                            = errors.New("login code input failed")
	errLoginButton                           = errors.New("login confirm button failed")
	errLoginCookie                           = errors.New("login cookie capture failed")
	errBrowserBusy                           = errors.New("browser capacity reached")
	errPlatformLogin                         = errors.New("platform login is required")
	accountSessions                          = accountLoginSessions{sessions: make(map[string]*accountLoginSession), current: make(map[string]string), creating: make(map[string]bool)}
	browserSlotsOnce                         sync.Once
	browserSlots                             chan struct{}
	accountServiceLife, cancelAccountService = context.WithCancel(context.Background())
	browserClosersMu                         sync.Mutex
	browserClosers                           = make(map[*rod.Page]func())
)

type accountLoginSessions struct {
	mu       sync.Mutex
	sessions map[string]*accountLoginSession
	current  map[string]string
	creating map[string]bool
}

type accountLoginSession struct {
	mu              sync.Mutex
	id              string
	owner           string
	expiresAt       time.Time
	resultExpiresAt time.Time
	state           string
	invalid         bool
	failure         error
	submitted       bool
	confirmed       bool
	staleInvalid    bool
	page            *rod.Page
	close           func()
	cancel          context.CancelFunc
	cookies         json.RawMessage
}

type loginSessionResponse struct {
	SessionID         string          `json:"session_id"`
	State             string          `json:"state"`
	ExpiresAt         time.Time       `json:"expires_at"`
	VerificationError *string         `json:"verification_error"`
	Cookies           json.RawMessage `json:"cookies,omitempty"`
}

type accountRequest struct {
	Owner     string          `json:"owner"`
	SessionID string          `json:"session_id"`
	Code      string          `json:"code"`
	Cookies   json.RawMessage `json:"cookies"`
	Keyword   string          `json:"keyword"`
	FeedID    string          `json:"feed_id"`
	XsecToken string          `json:"xsec_token"`
}

func readAccountRequest(c *gin.Context, limit int64) (*accountRequest, bool) {
	c.Request.Body = http.MaxBytesReader(c.Writer, c.Request.Body, limit)
	decoder := json.NewDecoder(c.Request.Body)
	decoder.DisallowUnknownFields()
	var request accountRequest
	if decoder.Decode(&request) != nil || decoder.Decode(&struct{}{}) != io.EOF {
		respondError(c, http.StatusUnprocessableEntity, "ACCOUNT_REQUEST_INVALID", "请求格式无效", nil)
		return nil, false
	}
	return &request, true
}

func validCookieDomain(domain string) bool {
	domain = strings.TrimPrefix(strings.ToLower(domain), ".")
	return domain == "xiaohongshu.com" || strings.HasSuffix(domain, ".xiaohongshu.com")
}

func validateAccountCookies(data json.RawMessage) bool {
	if len(data) == 0 || len(data) > 100000 {
		return false
	}
	var values []*proto.NetworkCookie
	if json.Unmarshal(data, &values) != nil || len(values) == 0 || len(values) > 100 {
		return false
	}
	for _, value := range values {
		if value == nil || value.Name == "" || value.Value == "" || !validCookieDomain(value.Domain) {
			return false
		}
	}
	return true
}

func initializeAccountBrowsers() {
	browserSlotsOnce.Do(func() {
		limit := 4
		if raw := os.Getenv("XHS_MAX_BROWSERS"); raw != "" {
			parsed, parseErr := strconv.Atoi(raw)
			if parseErr != nil || parsed < 1 || parsed > 64 {
				panic("XHS_MAX_BROWSERS must be between 1 and 64")
			}
			limit = parsed
		}
		browserSlots = make(chan struct{}, limit)
	})
}

// Each launcher uses its own temporary profile; its slot includes the complete QR lifetime.
func newAccountPage(ctx context.Context, cookies json.RawMessage) (page *rod.Page, closePage func(), err error) {
	if accountServiceLife.Err() != nil || ctx.Err() != nil {
		return nil, nil, errLoginFailed
	}
	browserCtx, cancelBrowser := context.WithCancel(ctx)
	stopShutdownCancellation := context.AfterFunc(accountServiceLife, cancelBrowser)
	initializeAccountBrowsers()
	select {
	case browserSlots <- struct{}{}:
	default:
		stopShutdownCancellation()
		cancelBrowser()
		return nil, nil, errBrowserBusy
	}
	var closeBrowser func()
	transferred := false
	defer func() {
		if failure := recover(); failure != nil {
			page, closePage, err = nil, nil, errLoginFailed
		}
		if !transferred {
			stopShutdownCancellation()
			cancelBrowser()
			if closeBrowser != nil {
				closeBrowser()
			}
			<-browserSlots
		}
	}()
	b := browser.NewBrowser(configs.IsHeadless(), browser.WithFingerprintSeed(configs.FingerprintSeed()), browser.WithProxy(configs.Proxy()), browser.WithContext(browserCtx), browser.WithCookies(string(cookies)))
	closeBrowser = func() {
		defer func() { _ = recover() }()
		closeCtx, cancel := context.WithTimeout(context.Background(), 5*time.Second)
		defer cancel()
		b.CloseWithContext(closeCtx)
	}
	page = b.NewPage()
	var once sync.Once
	closePage = func() {
		once.Do(func() {
			defer func() { <-browserSlots }()
			browserClosersMu.Lock()
			delete(browserClosers, page)
			browserClosersMu.Unlock()
			stopShutdownCancellation()
			cancelBrowser()
			closeBrowser()
		})
	}
	browserClosersMu.Lock()
	browserClosers[page] = closePage
	browserClosersMu.Unlock()
	transferred = true
	return page, closePage, nil
}

func stopAccountBrowsers() {
	cancelAccountService()
	browserClosersMu.Lock()
	closers := make([]func(), 0, len(browserClosers))
	for _, closeBrowser := range browserClosers {
		closers = append(closers, closeBrowser)
	}
	browserClosersMu.Unlock()
	var waiting sync.WaitGroup
	for _, closeBrowser := range closers {
		waiting.Add(1)
		go func(closeBrowser func()) { defer waiting.Done(); closeBrowser() }(closeBrowser)
	}
	waiting.Wait()
}

// SMS fields must belong to one visible captcha dialog; never join unrelated page fields.
const loginDOMScript = `
	const visible = el => el && el.getBoundingClientRect().width > 0 && el.getBoundingClientRect().height > 0
		&& getComputedStyle(el).visibility !== 'hidden' && getComputedStyle(el).display !== 'none';
	const inputSelector = 'input.r-input-inner[placeholder="请输入验证码"],input[autocomplete="one-time-code"]';
	const confirmLabel = /^(确认|提交|验证|确定|确认验证|验证中|正在验证)$/;
	const headers = [...document.querySelectorAll('.captcha-modal__header,.captcha-modal-title')].filter(visible);
	const findSMSRoot = () => {
		const roots = new Set();
		for (const header of headers) {
			let root = header.parentElement;
			for (let depth = 0; root && root !== document.body && depth < 6; depth++, root = root.parentElement) {
				if (!root.matches('[class*="captcha-modal"],[role="dialog"]')) continue;
				const inputs = [...root.querySelectorAll(inputSelector)].filter(visible);
				const buttons = [...root.querySelectorAll('.btn.btn-block')].filter(el => visible(el) && confirmLabel.test(el.textContent.trim()));
				if (inputs.length === 1 && buttons.length === 1) { roots.add(root); break; }
			}
		}
		return roots.size === 1 ? [...roots][0] : null;
	};
`

// Only inspect the existing QR browser; polling must not navigate or create a new browser.
const loginStateScript = `() => {` + loginDOMScript + `
	const unwrap = value => value && (value.value ?? value._value ?? value._rawValue ?? value);
	const user = unwrap(window.__INITIAL_STATE__?.user?.userInfo);
	const loggedIn = visible(document.querySelector('.main-container .user .link-wrapper .channel'))
		&& user && user.guest !== true && typeof (user.userId || user.user_id) === 'string'
		&& Boolean(user.userId || user.user_id);
	const smsRoot = findSMSRoot();
	const otherChallenge = [...document.querySelectorAll('.geetest_panel,.geetest_holder,.mask-slider-captcha,.point-captcha,.rotate-captcha,.red-captcha-slider')]
		.some(visible) || headers.some(el => /滑块|拼图|拖动|点选|旋转/.test(el.textContent));
	const pending = smsRoot && ([...smsRoot.querySelectorAll(inputSelector)].some(el => el.disabled || el.readOnly)
		|| [...smsRoot.querySelectorAll('.btn.btn-block')].some(el => visible(el) && /验证中|正在验证/.test(el.textContent)));
	const invalid = smsRoot && [...smsRoot.querySelectorAll('.error-box span')]
		.some(el => visible(el) && /验证码.*(错误|失效|过期|不正确)|验证码不正确/.test(el.textContent));
	return JSON.stringify({logged_in: Boolean(loggedIn), sms: Boolean(smsRoot), challenge: Boolean(otherChallenge), pending: Boolean(pending || (!smsRoot && headers.length)), invalid: Boolean(invalid)});
}`

func inspectLoginPage(ctx context.Context, page *rod.Page) (state string, invalid bool, err error) {
	info, err := (proto.TargetGetTargetInfo{TargetID: page.TargetID}).Call(page.Context(ctx).Timeout(5 * time.Second))
	if err != nil {
		return "", false, errLoginCheck
	}
	origin, err := url.Parse(info.TargetInfo.URL)
	if err != nil || origin.Scheme != "https" || (origin.Hostname() != "www.xiaohongshu.com" && origin.Hostname() != "xiaohongshu.com") {
		return "verification_required", false, nil
	}
	result, err := page.Context(ctx).Timeout(5 * time.Second).Eval(loginStateScript)
	if err != nil {
		if transientLoginPageError(err) {
			return "verifying", false, nil
		}
		return "", false, errLoginCheck
	}
	var flags struct {
		LoggedIn  bool `json:"logged_in"`
		SMS       bool `json:"sms"`
		Challenge bool `json:"challenge"`
		Pending   bool `json:"pending"`
		Invalid   bool `json:"invalid"`
	}
	if json.Unmarshal([]byte(result.Value.String()), &flags) != nil {
		return "", false, errLoginCheck
	}
	switch {
	case flags.Challenge:
		return "verification_required", false, nil
	case flags.Pending:
		return "verifying", false, nil
	case flags.SMS:
		return "sms_required", flags.Invalid, nil
	case flags.LoggedIn:
		return "logged_in", false, nil
	default:
		return "awaiting_scan", false, nil
	}
}

func transientLoginPageError(err error) bool {
	var missing *rod.ObjectNotFoundError
	return errors.Is(err, cdp.ErrCtxNotFound) || errors.Is(err, cdp.ErrCtxDestroyed) || errors.Is(err, cdp.ErrObjNotFound) || errors.As(err, &missing)
}

func (s *accountLoginSession) closeLocked() {
	if s.close != nil {
		closeBrowser := s.close
		s.close, s.page = nil, nil
		s.cancel()
		closeBrowser()
	}
}

func (l *accountLoginSessions) find(owner, id string) (*accountLoginSession, error) {
	l.mu.Lock()
	defer l.mu.Unlock()
	session := l.sessions[id]
	if !loginSessionID.MatchString(owner) || !loginSessionID.MatchString(id) || session == nil || session.owner != owner {
		return nil, errLoginGone
	}
	return session, nil
}

func (l *accountLoginSessions) remove(session *accountLoginSession) {
	l.mu.Lock()
	defer l.mu.Unlock()
	if l.sessions[session.id] == session {
		delete(l.sessions, session.id)
	}
	if l.current[session.owner] == session.id {
		delete(l.current, session.owner)
	}
}

func (s *accountLoginSession) inspectLocked(ctx context.Context) error {
	if s.failure != nil {
		return s.failure
	}
	if s.state == "logged_in" {
		if time.Now().Before(s.resultExpiresAt) {
			return nil
		}
		s.cookies, s.state = nil, "expired"
		return errLoginExpiry
	}
	if s.state == "expired" || !time.Now().Before(s.expiresAt) {
		s.state = "expired"
		s.closeLocked()
		return errLoginExpiry
	}
	ctx, cancel := context.WithDeadline(ctx, s.expiresAt)
	defer cancel()
	state, invalid, err := inspectLoginPage(ctx, s.page)
	if err != nil {
		return err
	}
	if s.staleInvalid && invalid {
		invalid = false
	} else {
		s.staleInvalid = false
	}
	if state == "awaiting_scan" && s.submitted {
		state = "verifying"
	}
	if state == "verifying" && s.submitted {
		s.confirmed, s.staleInvalid = true, false
	}
	if state == "sms_required" && s.confirmed && !invalid {
		state = "verifying"
	}
	s.state, s.invalid = state, invalid
	if invalid {
		s.submitted, s.confirmed = false, false
	}
	if state == "logged_in" {
		captureCtx, cancelCapture := context.WithTimeout(context.Background(), 5*time.Second)
		values, captureErr := s.page.Browser().Context(captureCtx).GetCookies()
		cancelCapture()
		if captureErr == nil {
			selected := make([]*proto.NetworkCookie, 0, len(values))
			for _, value := range values {
				if value != nil && value.Name != "" && value.Value != "" && validCookieDomain(value.Domain) {
					selected = append(selected, value)
				}
			}
			s.cookies, captureErr = json.Marshal(selected)
			if captureErr == nil && !validateAccountCookies(s.cookies) {
				captureErr = errLoginCookie
			}
		}
		if captureErr != nil {
			s.cookies, s.failure = nil, errLoginCookie
			s.closeLocked()
			return errLoginCookie
		}
		s.resultExpiresAt = time.Now().Add(loginLifetime)
		s.closeLocked()
	}
	return nil
}

func (s *accountLoginSession) responseLocked() *loginSessionResponse {
	response := &loginSessionResponse{SessionID: s.id, State: s.state, ExpiresAt: s.expiresAt, Cookies: s.cookies}
	if s.invalid {
		value := "invalid_code"
		response.VerificationError = &value
	}
	return response
}

func (s *accountLoginSession) watch(ctx context.Context) {
	ticker := time.NewTicker(time.Second)
	defer ticker.Stop()
	for {
		select {
		case <-ctx.Done():
			s.mu.Lock()
			if s.page != nil {
				s.state = "expired"
				s.closeLocked()
			}
			s.mu.Unlock()
			s.expireRetained()
			return
		case <-ticker.C:
			s.mu.Lock()
			_ = s.inspectLocked(ctx)
			done := s.page == nil
			s.mu.Unlock()
			if done {
				s.expireRetained()
				return
			}
		}
	}
}

func (s *accountLoginSession) expireRetained() {
	// Completed cookies are a retryable hand-off, never durable service storage.
	timer := time.NewTimer(loginLifetime)
	defer timer.Stop()
	<-timer.C
	s.mu.Lock()
	s.cookies, s.state = nil, "expired"
	s.closeLocked()
	s.mu.Unlock()
	accountSessions.remove(s)
}

func loginAccountError(c *gin.Context, err error) {
	status, code := http.StatusBadGateway, "LOGIN_SESSION_FAILED"
	switch err {
	case errLoginGone:
		status, code = http.StatusConflict, "LOGIN_SESSION_UNAVAILABLE"
	case errLoginExpiry:
		status, code = http.StatusGone, "LOGIN_SESSION_EXPIRED"
	case errLoginSMS:
		status, code = http.StatusConflict, "LOGIN_SMS_NOT_REQUIRED"
	case errLoginCheck:
		code = "LOGIN_PAGE_CHECK_FAILED"
	case errLoginInput:
		code = "LOGIN_CODE_INPUT_FAILED"
	case errLoginButton:
		code = "LOGIN_CONFIRM_BUTTON_FAILED"
	case errLoginCookie:
		code = "LOGIN_COOKIE_SAVE_FAILED"
	case errBrowserBusy:
		status, code = http.StatusServiceUnavailable, "BROWSER_CAPACITY_REACHED"
	case errPlatformLogin:
		status, code = http.StatusUnauthorized, "PLATFORM_LOGIN_REQUIRED"
	}
	logrus.WithField("code", code).Warn("account source operation did not complete")
	respondError(c, status, code, "来源操作未完成", nil)
}

func (s *AppServer) accountLoginQrcodeHandler(c *gin.Context) {
	request, ok := readAccountRequest(c, 1024)
	if !ok {
		return
	}
	if !loginSessionID.MatchString(request.Owner) {
		respondError(c, 422, "OWNER_INVALID", "登录归属无效", nil)
		return
	}
	accountSessions.mu.Lock()
	if accountSessions.creating[request.Owner] {
		accountSessions.mu.Unlock()
		loginAccountError(c, errBrowserBusy)
		return
	}
	accountSessions.creating[request.Owner] = true
	previous := accountSessions.sessions[accountSessions.current[request.Owner]]
	accountSessions.mu.Unlock()
	defer func() {
		accountSessions.mu.Lock()
		delete(accountSessions.creating, request.Owner)
		accountSessions.mu.Unlock()
	}()
	if previous != nil {
		previous.mu.Lock()
		previous.cookies, previous.state = nil, "expired"
		previous.closeLocked()
		previous.mu.Unlock()
		accountSessions.remove(previous)
	}
	lifeCtx, cancelLife := context.WithTimeout(context.Background(), loginLifetime+45*time.Second)
	ctx, cancelRequest := context.WithTimeout(c.Request.Context(), 45*time.Second)
	defer cancelRequest()
	stopRequestCancellation := context.AfterFunc(ctx, cancelLife)
	defer stopRequestCancellation()
	page, closeBrowser, err := newAccountPage(lifeCtx, nil)
	if err != nil {
		cancelLife()
		loginAccountError(c, err)
		return
	}
	transferred := false
	defer func() {
		if !transferred {
			cancelLife()
			closeBrowser()
		}
		if recover() != nil {
			loginAccountError(c, errLoginFailed)
		}
	}()
	img, loggedIn, err := xiaohongshu.NewLogin(page).FetchQrcodeImage(ctx)
	if err != nil || loggedIn {
		loginAccountError(c, errLoginFailed)
		return
	}
	randomID := make([]byte, 24)
	if _, err := rand.Read(randomID); err != nil {
		loginAccountError(c, errLoginFailed)
		return
	}
	if !stopRequestCancellation() || ctx.Err() != nil {
		loginAccountError(c, errLoginFailed)
		return
	}
	expiresAt := time.Now().UTC().Add(loginLifetime)
	watchCtx, cancelWatch := context.WithDeadline(lifeCtx, expiresAt)
	session := &accountLoginSession{id: hex.EncodeToString(randomID), owner: request.Owner, expiresAt: expiresAt, state: "awaiting_scan", page: page, close: closeBrowser, cancel: func() { cancelWatch(); cancelLife() }}
	accountSessions.mu.Lock()
	accountSessions.sessions[session.id], accountSessions.current[request.Owner] = session, session.id
	accountSessions.mu.Unlock()
	transferred = true
	go session.watch(watchCtx)
	respondSuccess(c, map[string]any{"img": img, "session_id": session.id, "expires_at": expiresAt}, "登录二维码已创建")
}

func (s *AppServer) accountLoginSessionHandler(c *gin.Context) {
	request, ok := readAccountRequest(c, 1024)
	if !ok {
		return
	}
	session, err := accountSessions.find(request.Owner, request.SessionID)
	if err != nil {
		loginAccountError(c, err)
		return
	}
	session.mu.Lock()
	defer session.mu.Unlock()
	if err := session.inspectLocked(c.Request.Context()); err != nil {
		loginAccountError(c, err)
		return
	}
	respondSuccess(c, session.responseLocked(), "登录状态已检查")
}

func (s *AppServer) accountLoginCancelHandler(c *gin.Context) {
	request, ok := readAccountRequest(c, 1024)
	if !ok {
		return
	}
	session, err := accountSessions.find(request.Owner, request.SessionID)
	if err != nil {
		loginAccountError(c, err)
		return
	}
	session.mu.Lock()
	session.cookies, session.state = nil, "expired"
	session.closeLocked()
	session.mu.Unlock()
	accountSessions.remove(session)
	respondSuccess(c, map[string]bool{"cancelled": true}, "登录会话已结束")
}

func (s *AppServer) accountLoginVerificationHandler(c *gin.Context) {
	request, ok := readAccountRequest(c, 1024)
	if !ok {
		return
	}
	if !loginCode.MatchString(request.Code) {
		respondError(c, 422, "LOGIN_VERIFICATION_INVALID", "验证码须为六位数字", nil)
		return
	}
	session, err := accountSessions.find(request.Owner, request.SessionID)
	if err != nil {
		loginAccountError(c, err)
		return
	}
	session.mu.Lock()
	defer session.mu.Unlock()
	if err := session.inspectLocked(c.Request.Context()); err != nil {
		loginAccountError(c, err)
		return
	}
	if session.state == "logged_in" || session.state == "verifying" {
		respondSuccess(c, session.responseLocked(), "登录状态已检查")
		return
	}
	if session.state != "sms_required" {
		loginAccountError(c, errLoginSMS)
		return
	}
	if err := session.submitCodeLocked(c.Request.Context(), request.Code); err != nil {
		loginAccountError(c, err)
		return
	}
	respondSuccess(c, session.responseLocked(), "验证码已提交")
}

func withAccountPage(c *gin.Context, operation func(context.Context, *rod.Page, *accountRequest) (any, error)) {
	request, ok := readAccountRequest(c, 110000)
	if !ok {
		return
	}
	if !validateAccountCookies(request.Cookies) {
		respondError(c, 422, "COOKIES_INVALID", "账户凭证格式无效", nil)
		return
	}
	ctx, cancel := context.WithTimeout(c.Request.Context(), 50*time.Second)
	defer cancel()
	page, closeBrowser, err := newAccountPage(ctx, request.Cookies)
	if err != nil {
		loginAccountError(c, err)
		return
	}
	defer closeBrowser()
	defer func() {
		if recover() != nil {
			loginAccountError(c, errLoginFailed)
		}
	}()
	result, err := operation(ctx, page, request)
	if err != nil {
		loginAccountError(c, err)
		return
	}
	respondSuccess(c, result, "来源读取已完成")
}

func (s *AppServer) accountSearchHandler(c *gin.Context) {
	withAccountPage(c, func(ctx context.Context, page *rod.Page, request *accountRequest) (any, error) {
		if strings.TrimSpace(request.Keyword) == "" || len([]rune(request.Keyword)) > 200 {
			return nil, errLoginFailed
		}
		if err := requireAccountLogin(ctx, page); err != nil {
			return nil, err
		}
		feeds, err := xiaohongshu.NewSearchAction(page).Search(ctx, request.Keyword, xiaohongshu.FilterOption{SortBy: "最新", PublishTime: "一天内"})
		if err != nil {
			return nil, err
		}
		return &FeedsListResponse{Feeds: feeds, Count: len(feeds)}, nil
	})
}

func (s *AppServer) accountDetailHandler(c *gin.Context) {
	withAccountPage(c, func(ctx context.Context, page *rod.Page, request *accountRequest) (any, error) {
		if !sourceID.MatchString(request.FeedID) || request.XsecToken == "" || len(request.XsecToken) > 1600 {
			return nil, errLoginFailed
		}
		if err := requireAccountLogin(ctx, page); err != nil {
			return nil, err
		}
		result, err := xiaohongshu.NewFeedDetailAction(page).GetFeedDetailWithConfig(ctx, request.FeedID, request.XsecToken, false, xiaohongshu.DefaultCommentLoadConfig())
		if err != nil {
			return nil, err
		}
		return &FeedDetailResponse{FeedID: request.FeedID, Data: result}, nil
	})
}

func requireAccountLogin(ctx context.Context, page *rod.Page) error {
	loggedIn, err := xiaohongshu.NewLogin(page).CheckLoginStatus(ctx)
	if err != nil {
		return errLoginCheck
	}
	if !loggedIn {
		return errPlatformLogin
	}
	return nil
}

func (s *accountLoginSession) submitCodeLocked(ctx context.Context, code string) error {
	ctx, cancel := context.WithDeadline(ctx, s.expiresAt)
	defer cancel()
	ctx, cancelOperation := context.WithTimeout(ctx, 18*time.Second)
	defer cancelOperation()
	page := s.page.Context(ctx).Timeout(5 * time.Second)
	root, err := page.Sleeper(rod.NotFoundSleeper).ElementByJS(rod.Eval(`() => {` + loginDOMScript + `return findSMSRoot(); }`))
	if err != nil {
		return errLoginInput
	}
	input, err := visibleLoginElement(root, `input.r-input-inner[placeholder="请输入验证码"],input[autocomplete="one-time-code"]`)
	if err != nil || input.SelectAllText() != nil {
		return errLoginInput
	}
	editable, err := input.Eval(`() => document.activeElement === this && !this.disabled && !this.readOnly`)
	if err != nil || !editable.Value.Bool() || page.InsertText(code) != nil {
		return errLoginInput
	}
	// Native insertion already emits input. Rod.Input emits it again, which can submit the code twice.
	s.submitted, s.confirmed, s.staleInvalid, s.invalid = true, false, s.invalid, false
	if err := s.inspectLocked(ctx); err != nil {
		return err
	}
	if s.state != "sms_required" || s.invalid {
		return nil
	}
	ticker := time.NewTicker(100 * time.Millisecond)
	defer ticker.Stop()
	for {
		select {
		case <-ctx.Done():
			if !time.Now().Before(s.expiresAt) {
				return s.inspectLocked(context.Background())
			}
			return errLoginButton
		case <-ticker.C:
		}
		if err := s.inspectLocked(ctx); err != nil {
			return err
		}
		if s.state != "sms_required" || s.invalid {
			return nil
		}
		// The input event may submit automatically, rebuild the dialog, or temporarily disable its button.
		button, err := s.page.Context(ctx).Timeout(3 * time.Second).Sleeper(rod.NotFoundSleeper).ElementByJS(rod.Eval(`code => {`+loginDOMScript+`
			const root = findSMSRoot();
			if (!root) return null;
			const inputs = [...root.querySelectorAll(inputSelector)].filter(el => visible(el) && !el.disabled && !el.readOnly);
			if (inputs.length !== 1 || inputs[0].value !== code) return null;
			const buttons = [...root.querySelectorAll('.btn.btn-block:not(.btn-disabled)')]
				.filter(el => visible(el) && !el.disabled && !el.hasAttribute('disabled') && /^(确认|提交|验证|确定|确认验证)$/.test(el.textContent.trim()));
			return buttons.length === 1 ? buttons[0] : null;
		}`, code))
		if err != nil {
			var notFound *rod.ElementNotFoundError
			if errors.As(err, &notFound) || transientLoginPageError(err) {
				continue
			}
			return errLoginButton
		}
		if err := button.Click(proto.InputMouseButtonLeft, 1); err != nil {
			if transientLoginPageError(err) {
				if checkErr := s.inspectLocked(ctx); checkErr != nil {
					return checkErr
				}
				if s.state != "sms_required" {
					return nil
				}
			}
			return errLoginButton
		}
		s.confirmed, s.staleInvalid = true, false
		if err := s.inspectLocked(ctx); err != nil {
			return err
		}
		if s.state == "sms_required" && !s.invalid {
			s.state = "verifying"
		}
		return nil
	}
}

func visibleLoginElement(root *rod.Element, selector string) (*rod.Element, error) {
	elements, err := root.Elements(selector)
	if err != nil {
		return nil, errLoginFailed
	}
	var selected *rod.Element
	for _, element := range elements {
		visible, err := element.Visible()
		if err != nil || !visible {
			continue
		}
		if selected != nil {
			return nil, errLoginFailed
		}
		selected = element
	}
	if selected == nil {
		return nil, errLoginFailed
	}
	return selected, nil
}
