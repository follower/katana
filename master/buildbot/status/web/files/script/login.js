$(document).ready(function() {	
	// validate the log in form

	jQuery.fn.center = function() {
	  this.css("position", "absolute");
	  this.css("top", ($(window).height() - this.outerHeight()) / 2 + $(window).scrollTop() + "px");
	  this.css("left", ($(window).width() - this.outerWidth()) / 2 + $(window).scrollLeft() + "px");
	  return this;
	};

	$(window).resize(function() {
		$('.more-info-box-js').center();
	});

	$('.more-info-box-js').center();

	function validatelogin() {
		var formEl = $('#loginForm');
		var loginBox = $('.login-box');
		var excludeFields = ':button, :hidden, :checkbox, :submit';
		var allInputs = $('input', formEl).not(excludeFields);
		$(allInputs).each(function(){
			$(this).keyup(function(){
				$(this).removeClass('not-valid');
				$(loginBox).removeClass('shake');
			});
		});
		$(loginBox).removeClass('shake');
		$('#submitLogin').click(function(e){
			$(loginBox).removeClass('shake');
			var validate = true;
			$(allInputs).each(function(){

				if ($(this).val() === "") {
					if ($(loginBox).hasClass('shake') == false) {
						$(loginBox).addClass('shake');
					}

					validate = false;

					$(this).addClass('not-valid');
						
					e.preventDefault();
					} else {
						$(this).removeClass('not-valid');
						$(loginBox).removeClass('shake');
				}

			});

				if (!validate && $('.error-input').length === 0) {
						$('.login-welcome-txt', loginBox).hide();
				$(formEl).prepend('<div class="error-input">Fill out the red fields before submitting</div>');
			}


		});
	};
	validatelogin();

});