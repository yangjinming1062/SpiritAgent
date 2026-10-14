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
	"regexp"
	"strings"
	"sync"
	"time"

	"github.com/gin-gonic/gin"
	"github.com/go-rod/rod"
	"github.com/go-rod/rod/lib/proto"
	"github.com/xpzouying/xiaohongshu-mcp/browser"
	"github.com/xpzouying/xiaohongshu-mcp/configs"
	"github.com/xpzouying/xiaohongshu-mcp/cookies"
	"github.com/xpzouying/xiaohongshu-mcp/xiaohongshu"
)

const loginLifetime = 4 * time.Minute

var (
	loginSessionID = regexp.MustCompile(`^[a-f0-9]{48}$`)
	loginCode      = regexp.MustCompile(`^[0-9]{6}$`)
	errLoginGone   = errors.New("login session is unavailable")
	errLoginExpiry = errors.New("login session has expired")
	errLoginFailed = errors.New("login session failed")
	errLoginSMS    = errors.New("login session is not awaiting an SMS code")
)

type adminLoginSessions struct {
	mutation sync.Mutex
	mu       sync.Mutex
	current  *adminLoginSession
}

type adminLoginSession struct {
	mu        sync.Mutex
	id        string
	expiresAt time.Time
	state     string
	invalid   bool
	failed    bool
	page      *rod.Page
	close     func()
	cancel    context.CancelFunc
}

type loginSessionResponse struct {
	SessionID         string    `json:"session_id"`
	State             string    `json:"state"`
	ExpiresAt         time.Time `json:"expires_at"`
	VerificationError *string   `json:"verification_error"`
}

// SMS fields must belong to one visible captcha dialog; never join unrelated page fields.
const loginDOMScript = `
	const visible = el => el && el.getBoundingClientRect().width > 0 && el.getBoundingClientRect().height > 0
		&& getComputedStyle(el).visibility !== 'hidden' && getComputedStyle(el).display !== 'none';
	const inputSelector = 'input.r-input-inner[placeholder="请输入验证码"],input[autocomplete="one-time-code"]';
	const confirmLabel = /^(确认|提交|验证|确定|确认验证)$/;
	const headers = [...document.querySelectorAll('.captcha-modal__header,.captcha-modal-title')].filter(visible);
	const findSMSRoot = () => {
		const roots = new Set();
		for (const header of headers) {
			let root = header.parentElement;
			for (let depth = 0; root && root !== document.body && depth < 6; depth++, root = root.parentElement) {
				if (!root.matches('[class*="captcha-modal"],[role="dialog"]')) continue;
				const inputs = [...root.querySelectorAll(inputSelector)].filter(el => visible(el) && !el.disabled && !el.readOnly);
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
	const otherChallenge = [...document.querySelectorAll('.captcha-modal__header,.captcha-modal-title,.geetest_panel,.geetest_holder,[role="dialog"]')]
		.some(el => visible(el) && /验证|滑块|拼图/.test(el.textContent));
	const invalid = smsRoot && [...smsRoot.querySelectorAll('.error-box span')]
		.some(el => visible(el) && /验证码.*(错误|失效|过期|不正确)|验证码不正确/.test(el.textContent));
	return JSON.stringify({logged_in: Boolean(loggedIn), sms: Boolean(smsRoot), challenge: Boolean(headers.length || otherChallenge), invalid: Boolean(invalid)});
}`

func inspectLoginPage(ctx context.Context, page *rod.Page) (state string, invalid bool, err error) {
	info, err := (proto.TargetGetTargetInfo{TargetID: page.TargetID}).Call(page.Context(ctx).Timeout(5 * time.Second))
	if err != nil {
		return "", false, errLoginFailed
	}
	origin, err := url.Parse(info.TargetInfo.URL)
	if err != nil || origin.Scheme != "https" || (origin.Hostname() != "www.xiaohongshu.com" && origin.Hostname() != "xiaohongshu.com") {
		return "verification_required", false, nil
	}
	result, err := page.Context(ctx).Timeout(5 * time.Second).Eval(loginStateScript)
	if err != nil {
		return "", false, errLoginFailed
	}
	var flags struct {
		LoggedIn  bool `json:"logged_in"`
		SMS       bool `json:"sms"`
		Challenge bool `json:"challenge"`
		Invalid   bool `json:"invalid"`
	}
	if json.Unmarshal([]byte(result.Value.String()), &flags) != nil {
		return "", false, errLoginFailed
	}
	switch {
	case flags.SMS:
		return "sms_required", flags.Invalid, nil
	case flags.Challenge:
		return "verification_required", false, nil
	case flags.LoggedIn:
		return "logged_in", false, nil
	default:
		return "awaiting_scan", false, nil
	}
}

func (s *adminLoginSession) closeLocked() {
	if s.close != nil {
		closeBrowser := s.close
		s.close = nil
		s.page = nil
		s.cancel()
		closeBrowser()
	}
}

func (l *adminLoginSessions) invalidate() {
	l.mu.Lock()
	s := l.current
	l.mu.Unlock()
	if s != nil {
		s.mu.Lock()
		s.state = "expired"
		s.closeLocked()
		s.mu.Unlock()
	}
}

func (l *adminLoginSessions) find(id string) (*adminLoginSession, error) {
	l.mu.Lock()
	defer l.mu.Unlock()
	if !loginSessionID.MatchString(id) || l.current == nil || l.current.id != id {
		return nil, errLoginGone
	}
	return l.current, nil
}

func (s *adminLoginSession) inspectLocked(ctx context.Context) error {
	if s.state == "expired" || (s.state != "logged_in" && !time.Now().Before(s.expiresAt)) {
		s.state = "expired"
		s.closeLocked()
		return errLoginExpiry
	}
	if s.failed {
		return errLoginFailed
	}
	if s.state == "logged_in" {
		return nil
	}
	ctx, cancel := context.WithDeadline(ctx, s.expiresAt)
	defer cancel()
	state, invalid, err := inspectLoginPage(ctx, s.page)
	if err != nil {
		return err
	}
	s.state, s.invalid = state, invalid
	if state == "logged_in" {
		if saveLoginCookies(ctx, s.page) != nil {
			s.failed = true
			s.closeLocked()
			return errLoginFailed
		}
		s.closeLocked()
	}
	return nil
}

func saveLoginCookies(ctx context.Context, page *rod.Page) error {
	ctx, cancel := context.WithTimeout(ctx, 5*time.Second)
	defer cancel()
	values, err := page.Browser().Context(ctx).GetCookies()
	if err != nil {
		return errLoginFailed
	}
	data, err := json.Marshal(values)
	if err != nil || ctx.Err() != nil || cookies.NewLoadCookie(cookies.GetCookiesFilePath()).SaveCookies(data) != nil {
		return errLoginFailed
	}
	return nil
}

func (s *adminLoginSession) responseLocked() *loginSessionResponse {
	response := &loginSessionResponse{SessionID: s.id, State: s.state, ExpiresAt: s.expiresAt}
	if s.invalid {
		value := "invalid_code"
		response.VerificationError = &value
	}
	return response
}

func (s *adminLoginSession) watch(ctx context.Context) {
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
			return
		case <-ticker.C:
			s.mu.Lock()
			_ = s.inspectLocked(ctx)
			done := s.page == nil
			s.mu.Unlock()
			if done {
				return
			}
		}
	}
}

func (s *XiaohongshuService) adminLoginQrcode(ctx context.Context) (result *LoginQrcodeResponse, err error) {
	ctx, cancelRequest := context.WithTimeout(ctx, 45*time.Second)
	defer cancelRequest()
	s.logins.mutation.Lock()
	defer s.logins.mutation.Unlock()
	if ctx.Err() != nil {
		return nil, errLoginFailed
	}
	s.logins.invalidate()
	// Browser and page event loops belong to the session, not to the QR HTTP request.
	lifeCtx, cancelLife := context.WithTimeout(context.Background(), loginLifetime+45*time.Second)
	stopRequestCancellation := context.AfterFunc(ctx, cancelLife)
	defer stopRequestCancellation()
	var closeBrowser func()
	transferred := false
	defer func() {
		if !transferred && closeBrowser != nil {
			closeBrowser()
		}
		if !transferred {
			cancelLife()
		}
		if recover() != nil {
			result, err = nil, errLoginFailed
		}
	}()
	b := browser.NewBrowser(configs.IsHeadless(), browser.WithFingerprintSeed(configs.FingerprintSeed()), browser.WithProxy(configs.Proxy()), browser.WithContext(lifeCtx))
	closeBrowser = func() {
		closeCtx, cancel := context.WithTimeout(context.Background(), 5*time.Second)
		defer cancel()
		b.CloseWithContext(closeCtx)
	}
	page := b.NewPage()
	closeAll := closeBrowser
	closeBrowser = func() {
		defer func() { _ = recover() }()
		defer closeAll()
		closeCtx, cancel := context.WithTimeout(context.Background(), 5*time.Second)
		defer cancel()
		_ = page.Context(closeCtx).Close()
	}
	img, loggedIn, err := xiaohongshu.NewLogin(page).FetchQrcodeImage(ctx)
	if err != nil {
		return nil, errLoginFailed
	}
	if loggedIn {
		state, _, checkErr := inspectLoginPage(ctx, page)
		if checkErr != nil || state != "logged_in" {
			return nil, errLoginFailed
		}
		return &LoginQrcodeResponse{IsLoggedIn: true, Timeout: "0s"}, nil
	}
	randomID := make([]byte, 24)
	if _, err := rand.Read(randomID); err != nil {
		return nil, errLoginFailed
	}
	if !stopRequestCancellation() || ctx.Err() != nil {
		return nil, errLoginFailed
	}
	expiresAt := time.Now().UTC().Add(loginLifetime)
	watchCtx, cancel := context.WithDeadline(lifeCtx, expiresAt)
	cancelSession := func() { cancel(); cancelLife() }
	session := &adminLoginSession{
		id: hex.EncodeToString(randomID), expiresAt: expiresAt, state: "awaiting_scan", page: page,
		close: closeBrowser, cancel: cancelSession,
	}
	s.logins.mu.Lock()
	s.logins.current = session
	s.logins.mu.Unlock()
	transferred = true
	go session.watch(watchCtx)
	return &LoginQrcodeResponse{IsLoggedIn: false, Timeout: loginLifetime.String(), Img: img, SessionID: session.id}, nil
}

func loginAdminHeaders() gin.HandlerFunc {
	return func(c *gin.Context) {
		if strings.HasPrefix(c.Request.URL.Path, "/api/v1/login/") {
			c.Header("Cache-Control", "no-store")
		}
		c.Next()
	}
}

func loginAdminError(c *gin.Context, err error) {
	status, code := http.StatusBadGateway, "LOGIN_SESSION_FAILED"
	switch err {
	case errLoginGone:
		status, code = http.StatusConflict, "LOGIN_SESSION_UNAVAILABLE"
	case errLoginExpiry:
		status, code = http.StatusGone, "LOGIN_SESSION_EXPIRED"
	case errLoginSMS:
		status, code = http.StatusConflict, "LOGIN_SMS_NOT_REQUIRED"
	}
	respondError(c, status, code, "登录操作未完成", nil)
}

func (s *AppServer) loginSessionHandler(c *gin.Context) {
	session, err := s.xiaohongshuService.logins.find(c.Query("session_id"))
	if err != nil {
		loginAdminError(c, err)
		return
	}
	session.mu.Lock()
	defer session.mu.Unlock()
	if err := session.inspectLocked(c.Request.Context()); err != nil {
		loginAdminError(c, err)
		return
	}
	respondSuccess(c, session.responseLocked(), "登录状态已检查")
}

func (s *AppServer) loginVerificationHandler(c *gin.Context) {
	c.Request.Body = http.MaxBytesReader(c.Writer, c.Request.Body, 1024)
	decoder := json.NewDecoder(c.Request.Body)
	decoder.DisallowUnknownFields()
	var request struct {
		SessionID string `json:"session_id"`
		Code      string `json:"code"`
	}
	if decoder.Decode(&request) != nil || decoder.Decode(&struct{}{}) != io.EOF || !loginSessionID.MatchString(request.SessionID) || !loginCode.MatchString(request.Code) {
		respondError(c, http.StatusUnprocessableEntity, "LOGIN_VERIFICATION_INVALID", "验证码须为六位数字", nil)
		return
	}
	session, err := s.xiaohongshuService.logins.find(request.SessionID)
	if err != nil {
		loginAdminError(c, err)
		return
	}
	session.mu.Lock()
	defer session.mu.Unlock()
	if err := session.inspectLocked(c.Request.Context()); err != nil {
		loginAdminError(c, err)
		return
	}
	if session.state == "logged_in" {
		respondSuccess(c, session.responseLocked(), "已登录")
		return
	}
	if session.state != "sms_required" {
		loginAdminError(c, errLoginSMS)
		return
	}
	operationCtx, cancelOperation := context.WithDeadline(c.Request.Context(), session.expiresAt)
	defer cancelOperation()
	page := session.page.Context(operationCtx).Timeout(5 * time.Second)
	root, err := page.Sleeper(rod.NotFoundSleeper).ElementByJS(rod.Eval(`() => {` + loginDOMScript + `return findSMSRoot(); }`))
	if err != nil {
		loginAdminError(c, errLoginSMS)
		return
	}
	input, err := visibleLoginElement(root, `input.r-input-inner[placeholder="请输入验证码"],input[autocomplete="one-time-code"]`)
	if err != nil || input.SelectAllText() != nil || input.Input(request.Code) != nil {
		loginAdminError(c, errLoginFailed)
		return
	}
	button, err := root.ElementByJS(rod.Eval(`() => {
		const visible = el => el && el.getBoundingClientRect().width > 0 && el.getBoundingClientRect().height > 0
			&& getComputedStyle(el).visibility !== 'hidden' && getComputedStyle(el).display !== 'none';
		const buttons = [...this.querySelectorAll('.btn.btn-block:not(.btn-disabled)')]
			.filter(el => visible(el) && !el.disabled && /^(确认|提交|验证|确定|确认验证)$/.test(el.textContent.trim()));
		return buttons.length === 1 ? buttons[0] : null;
	}`))
	if err != nil || button.Click(proto.InputMouseButtonLeft, 1) != nil {
		loginAdminError(c, errLoginFailed)
		return
	}
	session.invalid = false
	if err := session.inspectLocked(c.Request.Context()); err != nil {
		loginAdminError(c, err)
		return
	}
	respondSuccess(c, session.responseLocked(), "验证码已提交")
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
