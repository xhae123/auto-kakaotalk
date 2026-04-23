-- Usage: osascript _send.applescript "<chat name>" "<message>" [send]
-- Cherry-picked from the kakaotalk-mac skill (send_via_ui.applescript).
-- Uses clipboard paste to preserve Hangul (IME-safe).

on run argv
	if (count of argv) < 2 then
		error "Usage: _send.applescript <chat> <message> [send]"
	end if
	set chatName to item 1 of argv
	set msgText to item 2 of argv
	set doSend to ((count of argv) ≥ 3 and (item 3 of argv) is "send")

	-- put message on clipboard (preserves Korean, avoids IME corruption)
	do shell script "printf %s " & quoted form of msgText & " | pbcopy"

	tell application "KakaoTalk" to activate
	delay 0.5

	tell application "System Events"
		tell process "KakaoTalk"
			set frontmost to true
			set targetWin to missing value
			repeat with w in (every window)
				if (name of w) is chatName then
					set targetWin to w
					exit repeat
				end if
			end repeat

			if targetWin is missing value then
				-- open chat from main window list via double-click
				set mainWin to window "카카오톡"
				set chatRows to rows of table 1 of scroll area 1 of mainWin
				set matchedRow to missing value
				repeat with r in chatRows
					try
						repeat with t in (static texts of UI element 1 of r)
							if value of t is chatName then
								set matchedRow to r
								exit repeat
							end if
						end repeat
					end try
					if matchedRow is not missing value then exit repeat
				end repeat
				if matchedRow is missing value then error "Chat not found: " & chatName
				set {rx, ry} to position of matchedRow
				set {rw, rh} to size of matchedRow
				set cx to (rx + (rw div 2)) as integer
				set cy to (ry + (rh div 2)) as integer
				do shell script "/opt/homebrew/bin/cliclick dc:" & cx & "," & cy
				delay 0.8
				repeat 20 times
					repeat with w in (every window)
						if (name of w) is chatName then
							set targetWin to w
							exit repeat
						end if
					end repeat
					if targetWin is not missing value then exit repeat
					delay 0.1
				end repeat
			end if

			if targetWin is missing value then error "Chat window did not open: " & chatName

			perform action "AXRaise" of targetWin
			delay 0.3

			-- Click the message input area.
			-- Offset of 70px from bottom matches KakaoTalk's single-line input height;
			-- may need adjustment if KakaoTalk UI layout changes.
			set {wx, wy} to position of targetWin
			set {ww, wh} to size of targetWin
			set icx to (wx + (ww div 2)) as integer
			set icy to (wy + wh - 70) as integer
			do shell script "/opt/homebrew/bin/cliclick c:" & icx & "," & icy
			delay 0.4

			-- paste via hardware keycode (V = 9), bypasses Korean IME
			-- cliclick t:v would be intercepted by IME and typed as "ㅍ"
			key code 9 using command down
			delay 0.5

			if doSend then
				key code 36
				return "SENT: " & chatName & " <- " & msgText
			else
				return "DRAFT (not sent): " & chatName & " <- " & msgText
			end if
		end tell
	end tell
end run
