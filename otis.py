import os
import random
import smtplib
import ssl
import string
import subprocess
import time
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from pathlib import Path

import markdown
import requests
from dotenv import load_dotenv

from venueQ import Data, VenueQNode, VenueQRoot, logger

OTIS_PDF_PATH = Path('/tmp/otis-pdf')
if not OTIS_PDF_PATH.exists():
	OTIS_PDF_PATH.mkdir()
	OTIS_PDF_PATH.chmod(0o777)


def send_email(subject: str, recipient: str, body: str):
	mail = MIMEMultipart('alternative')
	mail['From'] = 'OTIS Overlord <evan@evanchen.cc>'
	mail['To'] = recipient
	mail['Subject'] = subject

	plain_msg = body
	plain_msg += '\n' * 2
	plain_msg += '**Evan Chen (陳誼廷)**<br>' + '\n'
	plain_msg += '[https://web.evanchen.cc](https://web.evanchen.cc/)'
	html_msg = markdown.markdown(plain_msg, extensions=['extra', 'sane_lists', 'smarty'])
	mail.attach(MIMEText(plain_msg, 'plain'))
	mail.attach(MIMEText(html_msg, 'html'))

	password = subprocess.run(
		['secret-tool', 'lookup', 'user', 'evanchen.mit', 'type', 'gmail'],
		text=True,
		capture_output=True
	).stdout

	session = smtplib.SMTP('smtp.gmail.com', 587)
	session.starttls(context=ssl.create_default_context())
	session.login('evanchen.mit@gmail.com', password)
	session.sendmail('evan@evanchen.cc', recipient, mail.as_string())


load_dotenv(Path('~/dotfiles/otis.env').expanduser())
TOKEN = os.getenv('OTIS_WEB_TOKEN')
assert TOKEN is not None
PRODUCTION = os.getenv('PRODUCTION', False)
if PRODUCTION:
	OTIS_API_URL = 'https://otis.evanchen.cc/aincrad/api/'
else:
	OTIS_API_URL = 'http://127.0.0.1:8000/aincrad/api/'


def query_otis_server(payload: Data) -> bool:
	payload['token'] = TOKEN
	resp = requests.post(OTIS_API_URL, data=payload)
	if resp.status_code == 200:
		logger.info("Got a 200 response back from server")
		subprocess.run(
			[Path('~/dotfiles/sh-scripts/noisemaker.sh').expanduser().absolute().as_posix(), '5']
		)
		return True
	else:
		logger.error(
			f"OTIS-WEB threw an exception with status code {resp.status_code}\n" +
			resp.content.decode('utf-8')
		)
		return False


class ProblemSet(VenueQNode):
	def get_initial_data(self) -> Data:
		return {
			'action': 'grade_problem_set',
		}

	def get_name(self, data: Data) -> str:
		return str(data['pk'])

	@property
	def pdf_target_path(self):
		def clean(key: str) -> str:
			return ''.join(c for c in self.data[key] if c in string.ascii_letters)

		fname = clean('student__user__first_name') + clean(
			'student__user__last_name'
		) + '-' + clean('unit__code') + '-' + clean('unit__group__name')
		return OTIS_PDF_PATH / f"otis_{fname}.pdf"

	def init_hook(self):
		self.data['approved'] = True
		if not self.pdf_target_path.exists():
			url = f"https://storage.googleapis.com/otisweb-media/{self.data['upload__content']}"
			pdf_response = requests.get(url=url)
			self.pdf_target_path.write_bytes(pdf_response.content)
			self.pdf_target_path.chmod(0o666)

	def on_buffer_open(self, data: Data):
		super().on_buffer_open(data)
		self.edit_temp(extension='mkd')
		subprocess.Popen(['zathura', self.pdf_target_path.absolute()])

	def on_buffer_close(self, data: Data):
		super().on_buffer_close(data)
		salutation = random.choice(["Hi", "Hello", "Hey"])
		closing = random.choice(
			[
				"Cheers",
				"Cheers",
				"Best",
				"Regards",
				"Warm wishes",
				"Later",
				"Cordially",
				"With appreciation",
				"Sincerely",
			]
		)

		comments_to_email = self.read_temp(extension='mkd').strip()
		body = comments_to_email
		body = f"{salutation} {data['student__user__first_name']}," + "\n\n" + body + "\n\n"
		if data.get('next_unit_to_unlock__pk', None):
			body += f"I unlocked {data['next_unit_to_unlock__code']} {data['next_unit_to_unlock__group__name']}."
			body += '\n\n'
		body += f"{closing},\n\nEvan (aka OTIS Overlord)"
		link = f"https://otis.evanchen.cc/dash/pset/{data['pk']}/"
		if data['approved'] and comments_to_email != '':
			body += '\n\n' + '-' * 40 + '\n\n'
			body += f"Earned: [{data.get('clubs', 0)} clubs and {data.get('hours', 0)} hearts]({link})" + '\n' * 2
			if data['feedback'] or data['special_notes']:
				body += r"```latex" + "\n"
				body += data['feedback']
				if data['feedback'] and data['special_notes']:
					body += '\n\n'
				body += data['special_notes']
				body += "\n" + r"```"
			recipient = data['student__user__email']
			subject = f"OTIS: {data['unit__code']} {data['unit__group__name']} checked off"
			try:
				send_email(subject=subject, recipient=recipient, body=body)
			except Exception as e:
				logger.error(f"Email {subject} to {recipient} failed", exc_info=e)
			else:
				logger.info(f"Email {subject} to {recipient} sent!")
			if query_otis_server(payload=data) is True:
				self.delete()


class ProblemSetCarrier(VenueQNode):
	def get_class_for_child(self, data: Data):
		return ProblemSet


class Inquiries(VenueQNode):
	def init_hook(self):
		self.data['approve_all'] = False

	def on_buffer_close(self, data: Data):
		super().on_buffer_close(data)
		if data['approve_all']:
			if query_otis_server(payload={'action': 'approve_inquiries'}):
				self.delete()


class Suggestion(VenueQNode):
	statement: str
	solution: str

	def get_name(self, data: Data) -> str:
		return str(data['pk'])

	def get_initial_data(self) -> Data:
		return {
			'action': 'mark_suggestion',
		}

	def init_hook(self):
		self.statement = self.data.pop('statement')
		self.solution = self.data.pop('solution')

	def on_buffer_open(self, data: Data):
		super().on_buffer_open(data)
		self.edit_temp(extension='mkd')
		tmp_path = f"/tmp/sg{int(time.time())}.tex"

		with open(tmp_path, 'w') as f:
			print(self.statement, file=f)
			print('\n---\n', file=f)
			if data['acknowledge'] is True:
				print(
					r'\emph{This problem and solution were contributed by ' + data['user__first_name'] +
					' ' + data['user__last_name'] + '}.',
					file=f
				)
				print('', file=f)
			print(self.solution, file=f)
		subprocess.Popen(
			[
				"xfce4-terminal",
				"-x",
				"python",
				"-m",
				"von",
				"add",
				data['source'],
				"-f",
				tmp_path,
			]
		)

	def on_buffer_close(self, data: Data):
		super().on_buffer_close(data)
		comments_to_email = self.read_temp(extension='mkd').strip()
		if comments_to_email != '':
			recipient = data['user__email']
			subject = f"OTIS: Suggestion {data['source']} processed"
			body = comments_to_email
			body += '\n\n' + '-' * 40 + '\n\n'
			body += r"```latex" + "\n"
			body += self.statement
			body += "\n" + r"```"
			try:
				send_email(subject=subject, recipient=recipient, body=body)
			except Exception as e:
				logger.error(f"Email {subject} to {recipient} failed", exc_info=e)
			else:
				logger.info(f"Email {subject} to {recipient} sent!")
			if query_otis_server(payload=data) is True:
				self.delete()


class SuggestionCarrier(VenueQNode):
	def get_class_for_child(self, data: Data):
		return Suggestion


class OTISRoot(VenueQRoot):
	def get_class_for_child(self, data: Data):
		if data['_name'] == 'Problem sets':
			return ProblemSetCarrier
		elif data['_name'] == 'Inquiries':
			return Inquiries
		elif data['_name'] == 'Suggestions':
			return SuggestionCarrier
		else:
			raise ValueError('wtf is ' + data['_name'])


if __name__ == "__main__":
	otis_response = requests.post(url=OTIS_API_URL, data={'token': TOKEN, 'action': 'init'})

	if PRODUCTION:
		otis_dir = Path('~/ProGamer/OTIS/queue').expanduser()
	else:
		otis_dir = Path('/tmp/otis-debug')
	if not otis_dir.exists():
		otis_dir.mkdir()
	ROOT_NODE = OTISRoot(otis_response.json(), root_dir=otis_dir)
