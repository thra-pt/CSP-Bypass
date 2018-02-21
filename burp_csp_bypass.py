"""
@author: moloch

This is a Burp plugin to parse Content-Security-Policy headers and detect
weaknesses and possibly bypasses in the policy.

UnregisteredDomains added by @t
"""
# pylint: disable=E0602,C0103,W0621,R0903,R0201

from socket import getaddrinfo
from httplib import HTTPResponse
from StringIO import StringIO
from urlparse import urlparse

from burp import IBurpExtender, IScannerCheck


class HttpDummySocket(object):

    """
    A dummy socket object so we can use httplib to parse the response bytearray
    """

    def __init__(self, byteResponse):
        self._file = StringIO(byteResponse)

    def makefile(self, *args, **kwargs):
        """ API compatability with `socket' """
        return self._file


class ContentSecurityPolicyScan(IScannerCheck):

    """ Implements the actual passive scan """

    def __init__(self, callbacks):
        """
        WARNING: Only one IScannerCheck object is ever created, because of this
        you effectively cannot use Python's `self' have to pass around any
        variables as method args if you want access to them, fml.
        """
        self._helpers = callbacks.getHelpers()

        # Checks must return a list of IScanIssue objects
        self._checks = [
            self.deprecatedHeaderCheck,
            self.reportOnlyHeaderCheck,
            self.unsafeContentSourceCheck,
            self.wildcardContentSourceCheck,
            self.wildcardSubdomainContentSourceCheck,
            self.insecureContentSourceCheck,
            self.nonceSourceCheck,
            self.missingDirectiveCheck,
            self.weakDefaultSourceCheck,
            self.knownBypassCheck,
        ]

    def _getUrl(self, burpHttpReqResp):
        """
        Uses the Burp helper APIs to get the URL from the HttpReqResp object
        """
        return self._helpers.analyzeRequest(burpHttpReqResp).getUrl()

    def doPassiveScan(self, burpHttpReqResp):
        """
        This is a callback method for Burp, its called for each HTTP req/resp.
        Returns a list of IScanIssue(s)
        """
        if len(burpHttpReqResp.getResponse()):
            return self.proccessHttpResponse(burpHttpReqResp)
        else:
            return []

    def consolidateDuplicateIssues(self, existingIssue, newIssue):
        """
        This is a callback method for Burp, and is used to cleanup duplicate
        findings.
        @return An indication of which issue(s) should be reported in the main
        Scanner results.
        <code>-1</code> to report the existing issue only
        <code>0</code> to report both issues
        <code>1</code> to report the new issue only
        """
        if existingIssue.getIssueName() == newIssue.getIssueName():
            return -1
        else:
            return 0

    def proccessHttpResponse(self, burpHttpReqResp):
        """ Processes only the HTTP repsonses with a CSP header """
        byteResponse = burpHttpReqResp.getResponse()
        httpSocket = HttpDummySocket(bytearray(byteResponse))
        response = HTTPResponse(httpSocket)
        response.begin()
        issues = []
        for header in response.getheaders():
            if header[0].lower() in ContentSecurityPolicy.HEADERS:
                findings = self.parseContentSecurityPolicy(header, burpHttpReqResp)
                issues.extend(findings)
        return issues

    def parseContentSecurityPolicy(self, cspHeader, burpHttpReqResp):
        """ Parses the CSP response header and searches for issues """
        csp = ContentSecurityPolicy(cspHeader[0], cspHeader[1])
        issues = []
        for check in self._checks:
            issues.extend(check(csp, burpHttpReqResp))
        return issues

    def deprecatedHeaderCheck(self, csp, burpHttpReqResp):
        """
        Checks for the use of a deprecated header such as `X-WebKit-CSP'
        """
        issues = []
        if csp.is_deprecated_header():
            deprecatedHeader = DeprecatedHeader(
                httpService=burpHttpReqResp.getHttpService(),
                url=self._getUrl(burpHttpReqResp),
                httpMessages=burpHttpReqResp,
                severity="Medium",
                confidence="Certain")
            issues.append(deprecatedHeader)
        return issues

    def reportOnlyHeaderCheck(self, csp, burpHttpReqResp):
        """
        Checks for the use of a report-only CSP header
        """
        issues = []
        if csp.is_report_only_mode():
            reportOnly = ReportOnlyHeader(
                httpService=burpHttpReqResp.getHttpService(),
                url=self._getUrl(burpHttpReqResp),
                httpMessages=burpHttpReqResp,
                severity="High",
                confidence="Certain")
            issues.append(reportOnly)
        return issues

    def unsafeContentSourceCheck(self, csp, burpHttpReqResp):
        """ Checks the current CSP header for unsafe content sources """
        issues = []
        for directive in [SCRIPT_SRC, STYLE_SRC]:
            if UNSAFE_EVAL in csp[directive] or UNSAFE_INLINE in csp[directive]:
                unsafeContent = UnsafeContentSource(
                    httpService=burpHttpReqResp.getHttpService(),
                    url=self._getUrl(burpHttpReqResp),
                    httpMessages=burpHttpReqResp,
                    severity="High",
                    confidence="Certain",
                    directive=directive)
                issues.append(unsafeContent)
        return issues

    def wildcardContentSourceCheck(self, csp, burpHttpReqResp):
        """
        Check content sources for wildcards '*' note that wilcard subdomains
        are checked by `wildcardSubdomainContentSourceCheck'
        """
        issues = []
        for directive, sources in csp.iteritems():
            if sources is None:
                continue  # Skip unspecified directives in NO_FALLBACK
            if any(src == "*" for src in sources):
                wildcardContent = WildcardContentSource(
                    httpService=burpHttpReqResp.getHttpService(),
                    url=self._getUrl(burpHttpReqResp),
                    httpMessages=burpHttpReqResp,
                    severity="Medium",
                    confidence="Certain",
                    directive=directive)
                issues.append(wildcardContent)
        return issues

    def wildcardSubdomainContentSourceCheck(self, csp, burpHttpReqResp):
        """ Check content sources for wildcards subdomains '*.foo.com' """
        issues = []
        for directive, sources in csp.iteritems():
            if sources is None:
                continue
            # This check is a little hacky but should work well
            # the shortest subdomain string should be like *.a.bc
            if any("*" in src and 5 <= len(src) for src in sources):
                wilcardSubdomain = WildcardSubdomainContentSource(
                    httpService=burpHttpReqResp.getHttpService(),
                    url=self._getUrl(burpHttpReqResp),
                    httpMessages=burpHttpReqResp,
                    severity="Low",
                    confidence="Certain",
                    directive=directive)
                issues.append(wilcardSubdomain)
        return issues

    def nonceSourceCheck(self, csp, burpHttpReqResp):
        """
        Check content sources for wildcards '*' note that wilcard subdomains
        are checked by `wildcardSubdomainContentSourceCheck'
        """
        issues = []
        for directive, sources in csp.iteritems():
            if sources is None:
                continue
            if any(src.startswith("'nonce-") for src in sources):
                nonceContent = NonceContentSource(
                    httpService=burpHttpReqResp.getHttpService(),
                    url=self._getUrl(burpHttpReqResp),
                    httpMessages=burpHttpReqResp,
                    severity="Informational",
                    confidence="Certain",
                    directive=directive)
                issues.append(nonceContent)
        return issues

    def insecureContentSourceCheck(self, csp, burpHttpReqResp):
        """ Check content sources that allow insecure network protocols """
        issues = []
        for directive, sources in csp.iteritems():
            if sources is None:
                continue
            for src in sources:
                if src == HTTP or urlparse(src).scheme in ["http", "ws"]:
                    insecureContent = InsecureContentDirective(
                        httpService=burpHttpReqResp.getHttpService(),
                        url=self._getUrl(burpHttpReqResp),
                        httpMessages=burpHttpReqResp,
                        severity="High",
                        confidence="Certain",
                        directive=directive)
                    issues.append(insecureContent)
        return issues

    def missingDirectiveCheck(self, csp, burpHttpReqResp):
        """
        Check for missing directives that do not inherit from `default-src'
        """
        issues = []
        for directive in ContentSecurityPolicy.NO_FALLBACK:
            if directive not in csp:
                missingDirective = MissingDirective(
                    httpService=burpHttpReqResp.getHttpService(),
                    url=self._getUrl(burpHttpReqResp),
                    httpMessages=burpHttpReqResp,
                    severity="Medium",
                    confidence="Certain",
                    directive=directive)
                issues.append(missingDirective)
        return issues

    def weakDefaultSourceCheck(self, csp, burpHttpReqResp):
        """
        Any `default-src' that is not 'none'/'self'/https: is considered weak
        """
        issues = []
        for contentSource in csp[DEFAULT_SRC]:
            if contentSource not in [SELF, NONE, HTTPS]:
                weakDefault = WeakDefaultSource(
                    httpService=burpHttpReqResp.getHttpService(),
                    url=self._getUrl(burpHttpReqResp),
                    httpMessages=burpHttpReqResp,
                    severity="Medium",
                    confidence="Certain")
                issues.append(weakDefault)
                break
        return issues

    def knownBypassCheck(self, csp, burpHttpReqResp):
        """
        Parses the CSP for known bypasses, this check is a little more
        complicated, and calls into other subroutines.
        """
        issues = []
        for directive, knownBypasses in CSP_KNOWN_BYPASSES.iteritems():
            bypasses = self._bypassCheckDirective(csp, directive, knownBypasses)
            for bypass in bypasses:
                bypassIssue = self._createBypassIssue(directive, bypass, burpHttpReqResp)
                issues.append(bypassIssue)
        return issues

    def _createBypassIssue(self, directive, bypass, burpHttpReqResp):
        """ Creates the KnownCSPBypass issue object """
        knownBypass = KnownCSPBypass(
            httpService=burpHttpReqResp.getHttpService(),
            url=self._getUrl(burpHttpReqResp),
            httpMessages=burpHttpReqResp,
            severity="High",
            confidence="Certain",
            directive=directive,
            bypass=bypass)
        return knownBypass

    def _bypassCheckDirective(self, csp, directive, knownBypasses):
        """
        Check an individual directive (e.g. `script-src') to see if it contains
        any domains that host known CSP bypasses.
        """
        bypasses = []
        for src in csp[directive]:
            if src.startswith("'") or src in [HTTP, HTTPS, DATA, BLOB]:
                continue  # We only care about domains

            # Iterate over all bypasses and check if `src' allows loading
            # content from `domain' if so, we have a bypass!
            for domain, payload in knownBypasses:
                if csp_match_domains(src, domain):
                    bypasses.append((domain, payload,))
        return bypasses

    def UnregisteredDomain(self, csp, burpHttpReqResp):
        """ Check if a domain can be resolved. If no, probably unregistered """
        issues = []
        for directive, sources in csp.iteritems():
            if sources is None:
                continue
            for src in sources:
                try:
                    getaddrinfo(urlparse(src).netloc)
                    unregisteredDomain = UnregisteredDomain(
                        httpService=burpHttpReqResp.getHttpService(),
                        url=self._getUrl(burpHttpReqResp),
                        httpMessages=burpHttpReqResp,
                        severity="High",
                        confidence="Firm",
                        directive=directive)
                    issues.append(unregisteredDomain)
                except:
                    continue
        return issues

class BurpExtender(IBurpExtender):

    """ Burp extension object """

    NAME = "CSP Bypass"

    def	registerExtenderCallbacks(self, callbacks):
        """ Entrypoint and setup """
        callbacks.setExtensionName(self.NAME)
        callbacks.registerScannerCheck(ContentSecurityPolicyScan(callbacks))
        print '[*] CSP-Bypass extension loaded successfully.'
