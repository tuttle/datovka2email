#!/usr/bin/python

"""
Script for unattended downloading and e-mailing of all sent and received
messages in the "Datove schranky" system.

See the README for details.
"""

__author__ = 'Vlada Macek'
__email__ = 'macek@sandbox.cz'
__version__ = '0.2'
__license__ = 'MIT'

SMTP_SERVER = '10.201.0.1'
SMTP_SENDER = 'macek@sandbox.cz'
SMTP_RECIPIENT_FOR_SENT = 'Sent Databox Msg <macek@sandbox.cz>'
SMTP_RECIPIENT_FOR_RECEIVED = 'Received Databox Msg <macek@sandbox.cz>'

import os
import sys
import logging
import smtplib
import datetime
import time
import mimetypes
import unicodedata
from email import encoders, Charset
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from email.mime.base import MIMEBase
from email.utils import formatdate

logging.basicConfig(level=logging.ERROR)

from datovka.datovka import gtk, GUI, ConfigManager, os_support

# Revert what datovka does -- we want the failures on console.
sys.excepthook = sys.__excepthook__

# Ensure we don't need to force downloads.
ConfigManager.AUTO_DOWNLOAD_WHOLE_MESSAGES = True

# Default encoding mode set to Quoted Printable. Acts globally!
Charset.add_charset('utf-8', Charset.QP, Charset.QP, 'utf-8')


def strip_accents(unistr):
    """
    Return the accent-stripped str for the given unistr (unicode is required).
    """
    return unicodedata.normalize('NFKD', unistr).encode('ascii', 'ignore')


class DatovkaToEmail(GUI):

    def pop_and_sendmail(self):
        """
        Main method. Gets all messages for all active accounts. For every
        not-yet-emailed message the mail with attachments is generated
        and sent. Then the session is terminated. 
        """
        self.on_sync_all_accounts_activate(None)

        smtp = smtplib.SMTP(SMTP_SERVER)

        for account in self.accounts:
            self.open_account(account)

            for is_sent_type, message in self.iterate_unmailed_messages():
                recip = SMTP_RECIPIENT_FOR_SENT if is_sent_type \
                        else SMTP_RECIPIENT_FOR_RECEIVED
                msg = self.create_mail_message(message, is_sent_type, recip)
                smtp.sendmail(SMTP_SENDER, recip, msg.as_string())

        smtp.quit()

        # Prevent 'called outside of a mainloop' error upon destroy.
        gtk.main_quit = lambda: None

        # Used to cleanup the sessions.
        self.on_window1_destroy(None)

    def iterate_unmailed_messages(self):
        mailedlist_fn = os.path.join(GUI.get_dsgui_private_dir(create=True),
                                     'mailed-message-ids.txt')
        if os.path.exists(mailedlist_fn):
            already_mailed_mids = set(int(mid) for mid in open(mailedlist_fn))
        else:
            already_mailed_mids = set()

        db = self.account.messages

        # We need to isolate the Alchemy requests, so get ids first.
        all_message_ids = tuple(m.dmID for m in db)

        for mid in all_message_ids:
            if mid not in already_mailed_mids:
                is_sent_type = db.is_sent(mid)
                message = db.get_message_by_id(mid)
                # Pass the message for processing.
                yield is_sent_type, message
                # Mark this message-id to avoid emailing it again.
                print >>open(mailedlist_fn, 'a'), mid

    def message_textbody(self, message):
        """
        Create a nice message text info.
        """
        output = []
        
        def add_row(heading, text):
            output.append(u"%-20s %s" % (heading, text or _("Not available")))
        
        def add_newline():
            output.append(u"")
        
        def add_heading(text):
            output.append(u"*** %s ***" % text)
        
        add_heading(_("Identification"))
        add_row(_("ID:"), message.dmID)
        add_row(_("Subject:"), message.dmAnnotation or "")
        
        if message._dmType and message._dmType in self.message_type_to_text:
          add_row(_("Message type:"),
                  self.message_type_to_text.get(message._dmType, message._dmType))
        
        add_newline()
        add_row(_("From:"), message.dmSender)
        add_row(_("Sender Address:"), message.dmSenderAddress)
        add_newline()
        add_row(_("To:"), message.dmRecipient)
        add_row(_("Recipient Address:"), message.dmRecipientAddress)
        add_newline()
        for name, value in (("dmSenderIdent",_("Our file mark")),
                            ("dmSenderRefNumber",_("Our reference number")),
                            ("dmRecipientIdent",_("Your file mark")),
                            ("dmRecipientRefNumber",_("Your reference number")),
                            ("dmToHands",_("To hands")),
                            ("dmLegalTitleLaw",_("Law")),
                            ("dmLegalTitleYear",_("Year")),
                            ("dmLegalTitleSect",_("Section")),
                            ("dmLegalTitlePar",_("Paragraph")),
                            ("dmLegalTitlePoint",_("Letter"))):
            if getattr(message, name):
                add_row(value+":", getattr(message, name))
        add_newline()

        add_heading(_("Status"))
        add_row(_("Delivery time:"), os_support.format_datetime(message.dmDeliveryTime))
        add_row(_("Acceptance time:"), os_support.format_datetime(message.dmAcceptanceTime))
        add_row(_("Status:"), "%d - %s" % (message.dmMessageStatus,
                                           message.get_status_description()))

        if message.dmEvents:
          add_row(_("Events:"), " ")
          for event in message.dmEvents:
            etime = event.dmEventTime
            if type(etime) in (str, unicode):
              if "." in etime:
                etime = datetime.datetime.strptime(etime, "%Y-%m-%d %H:%M:%S.%f")
              else:
                etime = datetime.datetime.strptime(etime, "%Y-%m-%d %H:%M:%S")
            add_row("  ", "%s - %s" % (os_support.format_datetime(etime),
                                       event.dmEventDescr))
        add_newline()
        
        add_heading(_("Signature"))
        if not message.message_verification_attempted():
            verified = _("Not present")
            add_row(_("Message signature:"), verified)
        
        else:
            warning = _("Message signature and content do not correspond!")
            verified = message.is_message_verified() and _("Valid") or \
                       _("Invalid")+" - "+warning
            add_row(_("Message signature:"), verified)
            if message.is_message_verified():
                # check signing certificate
                date = self._get_message_verification_date(message)
                verified = message.was_signature_valid_at_date(
                                      date,
                                      ignore_missing_crl_check=not ConfigManager.CHECK_CRL)
                verified_text = verified and _("Valid") or _("Invalid")
                if not ConfigManager.CHECK_CRL:
                  verified_text += " (%s)" % _("Certificate revocation check is turned off!")
                
                add_row(_("Signing certificate:"), verified_text)
        
        tst_check = message.check_timestamp()
        if tst_check == None:
            tst_text = _("Not present")
        else:
            timestamp_check = tst_check and _("Valid") or _("Invalid!")
            if message.tstamp_token:
                timestamp_time = message.tstamp_token.get_genTime_as_datetime()
                tst_text = "%s (%s)" % (timestamp_check, os_support.format_datetime(timestamp_time))
            else:
                tst_text = timestamp_check
        
        add_row(_("Timestamp:"), tst_text)
        add_newline()
        
        return u"\n".join(output)

    def create_mail_message(self, message, is_sent_type, to_hdr):
        """
        Format the MIME mail structure.
        """
        outer = MIMEMultipart()
        outer['Subject'] = u'[%s %s %s] %s' % (self.account.credentials.username,
                                               is_sent_type and "SENT" or "RECEIVED",
                                               message.dmID,
                                               message.dmAnnotation)
        outer['From'] = SMTP_SENDER
        outer['To'] = to_hdr

        body = self.message_textbody(message)

        date = message.dmDeliveryTime if is_sent_type else message.dmAcceptanceTime
        if not date:
            date = datetime.datetime.now()
            body = u"WARNING: CURRENT TIME USED AS A MESSAGE DATE!\n\n" + body

        outer['Date'] = formatdate(time.mktime(date.timetuple()), localtime=True)

        body += u"\n*** Attachments ***\n"
        for attachment in message.dmFiles:
            body += attachment._dmFileDescr + u"\n"

        outer.preamble = 'Please use the MIME-aware mail reader.\n'

        part = MIMEText(body.encode('UTF-8'), _charset='UTF-8')
        outer.attach(part)

        for attachment in message.dmFiles:
            filename = strip_accents(attachment._dmFileDescr)
            ctype = attachment._dmMimeType
            if not ctype or ('/' not in ctype):
                ctype, encoding = mimetypes.guess_type(filename)
                if ctype is None or encoding is not None:
                    ctype = 'application/octet-stream'
            maintype, subtype = ctype.split('/', 1)
            part = MIMEBase(maintype, subtype)
            part.set_payload(attachment.get_decoded_content())
            encoders.encode_base64(part)
            part.add_header('Content-Disposition', 'attachment', filename=filename)
            outer.attach(part)

        return outer

    def _show_error(self, message, exception=None):
        print >>sys.stderr, "datovka2email EXCEPTION:", exception
        print >>sys.stderr, message


if __name__ == '__main__':
    app = DatovkaToEmail()
    app.pop_and_sendmail()
